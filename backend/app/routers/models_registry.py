import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.metrics import BACKEND_ERRORS
from app.models import ModelTransitionLog, ModelVersion, User
from app.models.model_registry import ModelVersionStage
from app.schemas.model_registry import (
    ModelTransitionRequest,
    ModelVersionList,
    ModelVersionRead,
    RegisteredModelSummary,
)
from app.services.mlflow_client import MlflowClient
from app.services.model_registry import InvalidTransitionError, validate_transition
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _mlflow() -> MlflowClient:
    return MlflowClient(settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)


@router.get("/versions/{version_id}", response_model=ModelVersionRead)
async def get_model_version_by_id(
    version_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    """Look up a ModelVersion by its UUID primary key.

    Used by Phase 13b SourceModelCard which receives ``source_model_version_id``
    from JobRead and needs to render the corresponding model card.
    """
    mv = (
        await session.execute(
            select(ModelVersion).where(ModelVersion.id == version_id)
        )
    ).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return ModelVersionRead.model_validate(mv)


@router.get("/versions", response_model=ModelVersionList)
async def list_model_versions_by_filter(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    source_job_id: uuid.UUID | None = Query(None),
) -> ModelVersionList:
    """List ModelVersions filtered by ``source_job_id``.

    Used by Phase 13b TrainedModelCard to find the model produced by a given
    train job. ``source_job_id`` is currently the only supported filter; calling
    this endpoint without it returns 400.
    """
    if source_job_id is None:
        raise HTTPException(
            status_code=400, detail="source_job_id query parameter required"
        )
    items = (
        await session.execute(
            select(ModelVersion)
            .where(ModelVersion.source_job_id == source_job_id)
            .order_by(ModelVersion.mlflow_version.desc())
        )
    ).scalars().all()
    return ModelVersionList(
        items=[ModelVersionRead.model_validate(m) for m in items],
        total=len(items),
        page=1,
        page_size=len(items) if items else 0,
    )


@router.get("", response_model=list[RegisteredModelSummary])
async def list_registered_models(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> list[RegisteredModelSummary]:
    stmt = (
        select(
            ModelVersion.mlflow_name,
            func.max(ModelVersion.mlflow_version).label("latest"),
        )
        .group_by(ModelVersion.mlflow_name)
    )
    names = (await session.execute(stmt)).all()

    summaries = []
    for name, latest in names:
        latest_prod = (await session.execute(
            select(func.max(ModelVersion.mlflow_version)).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
            )
        )).scalar_one()
        latest_staging = (await session.execute(
            select(func.max(ModelVersion.mlflow_version)).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.current_stage == ModelVersionStage.STAGING,
            )
        )).scalar_one()
        summaries.append(RegisteredModelSummary(
            name=name,
            latest_version=latest,
            latest_production_version=latest_prod,
            latest_staging_version=latest_staging,
        ))
    return summaries


@router.get("/{name}", response_model=RegisteredModelSummary)
async def get_registered_model(
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> RegisteredModelSummary:
    stmt = select(func.max(ModelVersion.mlflow_version)).where(
        ModelVersion.mlflow_name == name,
    )
    latest = (await session.execute(stmt)).scalar_one()
    if latest is None:
        raise HTTPException(status_code=404, detail="model not found")
    latest_prod = (await session.execute(
        select(func.max(ModelVersion.mlflow_version)).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
        )
    )).scalar_one()
    latest_staging = (await session.execute(
        select(func.max(ModelVersion.mlflow_version)).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.current_stage == ModelVersionStage.STAGING,
        )
    )).scalar_one()
    return RegisteredModelSummary(
        name=name,
        latest_version=latest,
        latest_production_version=latest_prod,
        latest_staging_version=latest_staging,
    )


@router.get("/{name}/versions", response_model=ModelVersionList)
async def list_model_versions(
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    stage: ModelVersionStage | None = None,
) -> ModelVersionList:
    filters = [ModelVersion.mlflow_name == name]
    if stage is not None:
        filters.append(ModelVersion.current_stage == stage)

    count = (await session.execute(
        select(func.count()).select_from(ModelVersion).where(*filters)
    )).scalar_one()
    items = (await session.execute(
        select(ModelVersion)
        .where(*filters)
        .order_by(ModelVersion.mlflow_version.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return ModelVersionList(
        items=[ModelVersionRead.model_validate(m) for m in items],
        total=count,
        page=page,
        page_size=page_size,
    )


@router.get("/{name}/versions/{version}", response_model=ModelVersionRead)
async def get_model_version(
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")
    return ModelVersionRead.model_validate(mv)


@router.post("/{name}/versions/{version}/transition", response_model=ModelVersionRead)
async def transition_model_version(
    name: str,
    version: int,
    body: ModelTransitionRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")

    try:
        validate_transition(
            mv.current_stage,
            body.to_stage,
            actor_role=user.role.value,
            is_owner=(mv.owner_id == user.id),
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    from_stage = mv.current_stage

    client = _mlflow()
    archive = body.to_stage == ModelVersionStage.PRODUCTION
    try:
        await client.transition_model_version_stage(
            name=name, version=str(version), stage=body.to_stage.value,
            archive_existing_versions=archive,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"MLflow transition failed: {e}")

    mv.current_stage = body.to_stage
    mv.last_transitioned_at = datetime.now(timezone.utc)

    if archive:
        others = (await session.execute(
            select(ModelVersion).where(
                ModelVersion.mlflow_name == name,
                ModelVersion.id != mv.id,
                ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
            )
        )).scalars().all()
        for o in others:
            session.add(ModelTransitionLog(
                model_version_id=o.id,
                from_stage=o.current_stage,
                to_stage=ModelVersionStage.ARCHIVED,
                actor_id=user.id,
                comment="auto-archived by transition to Production",
            ))
            o.current_stage = ModelVersionStage.ARCHIVED
            o.last_transitioned_at = datetime.now(timezone.utc)

    session.add(ModelTransitionLog(
        model_version_id=mv.id,
        from_stage=from_stage,
        to_stage=body.to_stage,
        actor_id=user.id,
        comment=body.comment,
    ))

    await session.commit()
    await session.refresh(mv)
    return ModelVersionRead.model_validate(mv)


@router.delete("/{name}/versions/{version}", status_code=204)
async def delete_model_version(
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.mlflow_name == name,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(status_code=404, detail="model version not found")
    if mv.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    if mv.current_stage not in (ModelVersionStage.NONE, ModelVersionStage.ARCHIVED):
        raise HTTPException(status_code=409, detail="must be stage=None or Archived")

    try:
        await _mlflow().delete_model_version(name, str(version))
    except Exception:
        BACKEND_ERRORS.labels(stage="mlflow_mv_delete").inc()
        logger.exception(
            "MLflow delete_model_version failed",
            extra={"mlflow_name": name, "mlflow_version": str(version)},
        )

    await session.delete(mv)
    await session.commit()
    return Response(status_code=204)
