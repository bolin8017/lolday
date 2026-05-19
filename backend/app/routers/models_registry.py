import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import get_mlflow
from app.models import (
    Detector,
    DetectorVersion,
    ModelOwnerTransferLog,
    ModelTransitionLog,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    ModelVisibilityLog,
    RegisteredModel,
    Role,
    User,
)
from app.models.detector import DetectorVersionStatus
from app.schemas.model_registry import (
    ModelTransitionRequest,
    ModelVersionList,
    ModelVersionRead,
    ModelVersionVisibilityUpdate,
    OwnerTransferRequest,
    RegisteredModelRead,
    RegisteredModelSummary,
    RegisteredModelUpdate,
)
from app.services.mlflow_client import MlflowClient
from app.services.model_registry import (
    InvalidTransitionError,
    resolve_registered_model,
    validate_transition,
)
from app.users import current_active_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _model_version_to_read(
    mv: ModelVersion,
    owner_handle: str,
    detector_name: str,
    detector_id: uuid.UUID,
    detector_version_tag: str,
    detector_version_status: DetectorVersionStatus,
) -> ModelVersionRead:
    """Construct ModelVersionRead with derived UI-friendly fields populated.

    The five trailing args are derived from joins against User, Detector, and
    DetectorVersion. Pass them explicitly so each call site is honest about
    its query shape (no lazy-load surprises in async sessions). The
    `detector_version_status` arg feeds the `is_runnable` derived field — a
    model can only be run when its training DV is still ACTIVE (see
    architecture.md §10 #22).
    """
    return ModelVersionRead(
        id=mv.id,
        mlflow_version=mv.mlflow_version,
        mlflow_run_id=mv.mlflow_run_id,
        current_stage=mv.current_stage,
        visibility=mv.visibility,
        detector_version_id=mv.detector_version_id,
        source_job_id=mv.source_job_id,
        owner_id=mv.owner_id,
        created_at=mv.created_at,
        last_transitioned_at=mv.last_transitioned_at,
        owner=owner_handle,
        name=detector_name,
        detector_id=detector_id,
        detector_version_tag=detector_version_tag,
        is_runnable=detector_version_status == DetectorVersionStatus.ACTIVE,
    )


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
    row = (
        await session.execute(
            select(
                ModelVersion,
                User.handle,
                Detector.name,
                Detector.id,
                DetectorVersion.git_tag,
                DetectorVersion.status,
            )
            .join(
                RegisteredModel, ModelVersion.registered_model_id == RegisteredModel.id
            )
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .join(
                DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id
            )
            .where(ModelVersion.id == version_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="model version not found")
    (
        mv,
        owner_handle,
        detector_name,
        detector_id,
        detector_version_tag,
        dv_status,
    ) = row
    return _model_version_to_read(
        mv,
        owner_handle,
        detector_name,
        detector_id,
        detector_version_tag,
        dv_status,
    )


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
    # Cap is defensive — current contract says one job → one model version,
    # but ModelVersion.source_job_id has no DB-level unique constraint, so a
    # bug in the registration projector could in theory produce duplicates.
    rows = (
        await session.execute(
            select(
                ModelVersion,
                User.handle,
                Detector.name,
                Detector.id,
                DetectorVersion.git_tag,
                DetectorVersion.status,
            )
            .join(
                RegisteredModel, ModelVersion.registered_model_id == RegisteredModel.id
            )
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .join(
                DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id
            )
            .where(ModelVersion.source_job_id == source_job_id)
            .order_by(ModelVersion.mlflow_version.desc())
            .limit(100)
        )
    ).all()
    items = [
        _model_version_to_read(mv, h, n, did, tag, status)
        for mv, h, n, did, tag, status in rows
    ]
    return ModelVersionList(
        items=items,
        total=len(items),
        page=1,
        page_size=len(items) if items else 0,
    )


@router.get("", response_model=list[RegisteredModelSummary])
async def list_models(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    owner: str | None = Query(default=None),
    visibility: Literal["all", "public", "mine"] = Query(default="all"),
) -> list[RegisteredModelSummary]:
    """List registered models visible to the caller.

    Visibility rule (per-version, not per-model):
    - Admins see all versions unconditionally.
    - Everyone else sees versions that are PUBLIC or owned by themselves.
    A model row is included only when at least one version passes the filter.

    Query params:
    - owner: filter by owner handle (post-visibility-filter).
    - visibility: "all" (default), "public" (models with ≥1 public version),
      "mine" (models owned by the caller).
    """
    visible: sa.ColumnElement[bool]
    if user.role == Role.ADMIN:
        visible = sa.true()
    else:
        visible = (ModelVersion.visibility == ModelVersionVisibility.PUBLIC) | (
            ModelVersion.owner_id == user.id
        )

    stmt = (
        select(
            User.handle.label("owner"),
            Detector.name.label("name"),
            RegisteredModel.description,
            RegisteredModel.tags,
            func.max(ModelVersion.mlflow_version).label("latest_version"),
            func.max(
                case(
                    (
                        ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                        ModelVersion.mlflow_version,
                    ),
                    else_=None,
                )
            ).label("latest_production_version"),
            func.max(
                case(
                    (
                        ModelVersion.current_stage == ModelVersionStage.STAGING,
                        ModelVersion.mlflow_version,
                    ),
                    else_=None,
                )
            ).label("latest_staging_version"),
        )
        .select_from(RegisteredModel)
        .join(User, RegisteredModel.owner_id == User.id)
        .join(Detector, RegisteredModel.detector_id == Detector.id)
        .join(ModelVersion, ModelVersion.registered_model_id == RegisteredModel.id)
        .where(visible)
        .group_by(RegisteredModel.id, User.handle, Detector.name)
    )

    if owner is not None:
        stmt = stmt.where(User.handle == owner)

    if visibility == "public":
        stmt = stmt.having(
            func.count(
                case(
                    (ModelVersion.visibility == ModelVersionVisibility.PUBLIC, 1),
                    else_=None,
                )
            )
            > 0
        )
    elif visibility == "mine":
        stmt = stmt.where(RegisteredModel.owner_id == user.id)

    rows = (await session.execute(stmt)).all()
    return [RegisteredModelSummary(**r._mapping) for r in rows]


def _summary_query_for_rm(
    rm_id: uuid.UUID,
    user: User,
) -> sa.Select[tuple[int | None, ...]]:
    """Build a select() returning (latest_version, latest_production_version,
    latest_staging_version) restricted to versions visible to ``user``.
    """
    visible: sa.ColumnElement[bool]
    if user.role == Role.ADMIN:
        visible = sa.true()
    else:
        visible = (ModelVersion.visibility == ModelVersionVisibility.PUBLIC) | (
            ModelVersion.owner_id == user.id
        )

    return select(  # type: ignore[return-value]  # sqlalchemy-stubs infers Select[tuple[int, Any, Any]]; actual runtime values are int | None
        func.max(ModelVersion.mlflow_version).label("latest_version"),
        func.max(
            case(
                (
                    ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                    ModelVersion.mlflow_version,
                ),
                else_=None,
            )
        ).label("latest_production_version"),
        func.max(
            case(
                (
                    ModelVersion.current_stage == ModelVersionStage.STAGING,
                    ModelVersion.mlflow_version,
                ),
                else_=None,
            )
        ).label("latest_staging_version"),
    ).where(ModelVersion.registered_model_id == rm_id, visible)


@router.get("/{owner}/{name}", response_model=RegisteredModelRead)
async def get_model(
    owner: str,
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user)
    summary = (await session.execute(_summary_query_for_rm(rm.id, user))).one()
    return RegisteredModelRead(
        owner=owner,
        name=name,
        description=rm.description,
        tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )


@router.get("/{owner}/{name}/versions", response_model=ModelVersionList)
async def list_versions(
    owner: str,
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionList:
    rm = await resolve_registered_model(owner, name, session, user)
    visible: sa.ColumnElement[bool]
    if user.role == Role.ADMIN:
        visible = sa.true()
    else:
        visible = (ModelVersion.visibility == ModelVersionVisibility.PUBLIC) | (
            ModelVersion.owner_id == user.id
        )
    rows = (
        await session.execute(
            select(ModelVersion, DetectorVersion.git_tag, DetectorVersion.status)
            .join(
                DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id
            )
            .where(ModelVersion.registered_model_id == rm.id, visible)
            .order_by(ModelVersion.mlflow_version.desc())
        )
    ).all()
    items = [
        _model_version_to_read(mv, owner, name, rm.detector_id, tag, status)
        for mv, tag, status in rows
    ]
    return ModelVersionList(items=items, total=len(items), page=1, page_size=len(items))


@router.get("/{owner}/{name}/versions/{version}", response_model=ModelVersionRead)
async def get_version(
    owner: str,
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user)
    row = (
        await session.execute(
            select(ModelVersion, DetectorVersion.git_tag, DetectorVersion.status)
            .join(
                DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id
            )
            .where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.mlflow_version == version,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "version not found")
    mv, detector_version_tag, dv_status = row
    is_owner = mv.owner_id == user.id
    is_admin = user.role.value == "admin"
    if mv.visibility == ModelVersionVisibility.PRIVATE and not (is_owner or is_admin):
        raise HTTPException(404, "version not found")  # hide-existence
    return _model_version_to_read(
        mv, owner, name, rm.detector_id, detector_version_tag, dv_status
    )


@router.post(
    "/{owner}/{name}/versions/{version}/transition",
    response_model=ModelVersionRead,
)
async def transition_model_version(
    owner: str,
    name: str,
    version: int,
    body: ModelTransitionRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow)],
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mv = (
        await session.execute(
            select(ModelVersion).where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.mlflow_version == version,
            )
        )
    ).scalar_one_or_none()
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
        raise HTTPException(status_code=403, detail=str(e)) from e

    from_stage = mv.current_stage

    # Capture mlflow_name for the MLflow API call (avoid lazy-load on relationships)
    detector_name = (
        await session.execute(
            select(Detector.name).where(Detector.id == rm.detector_id)
        )
    ).scalar_one()
    owner_handle = (
        await session.execute(select(User.handle).where(User.id == rm.owner_id))
    ).scalar_one()
    mlflow_name = f"{owner_handle}/{detector_name}"

    archive = body.to_stage == ModelVersionStage.PRODUCTION
    try:
        await client.transition_model_version_stage(
            name=mlflow_name,
            version=str(version),
            stage=body.to_stage.value,
            archive_existing_versions=archive,
        )
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"MLflow transition failed: {e}"
        ) from e

    mv.current_stage = body.to_stage
    mv.last_transitioned_at = datetime.now(UTC)

    if archive:
        # Auto-archive other Production versions in the same RegisteredModel namespace
        others = (
            (
                await session.execute(
                    select(ModelVersion).where(
                        ModelVersion.registered_model_id == rm.id,
                        ModelVersion.id != mv.id,
                        ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                    )
                )
            )
            .scalars()
            .all()
        )
        for o in others:
            session.add(
                ModelTransitionLog(
                    model_version_id=o.id,
                    from_stage=o.current_stage,
                    to_stage=ModelVersionStage.ARCHIVED,
                    actor_id=user.id,
                    comment="auto-archived by transition to Production",
                )
            )
            o.current_stage = ModelVersionStage.ARCHIVED
            o.last_transitioned_at = datetime.now(UTC)

    session.add(
        ModelTransitionLog(
            model_version_id=mv.id,
            from_stage=from_stage,
            to_stage=body.to_stage,
            actor_id=user.id,
            comment=body.comment,
        )
    )

    await session.commit()
    await session.refresh(mv)
    dv_tag, dv_status = (
        await session.execute(
            select(DetectorVersion.git_tag, DetectorVersion.status).where(
                DetectorVersion.id == mv.detector_version_id
            )
        )
    ).one()
    return _model_version_to_read(
        mv,
        owner_handle,
        detector_name,
        rm.detector_id,
        dv_tag,
        dv_status,
    )


@router.patch(
    "/{owner}/{name}/versions/{version}/visibility",
    response_model=ModelVersionRead,
)
async def update_visibility(
    owner: str,
    name: str,
    version: int,
    body: ModelVersionVisibilityUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> ModelVersionRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    row = (
        await session.execute(
            select(ModelVersion, DetectorVersion.git_tag, DetectorVersion.status)
            .join(
                DetectorVersion, DetectorVersion.id == ModelVersion.detector_version_id
            )
            .where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.mlflow_version == version,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(404, "version not found")
    mv, detector_version_tag, dv_status = row

    if mv.visibility == body.visibility:
        return _model_version_to_read(
            mv, owner, name, rm.detector_id, detector_version_tag, dv_status
        )  # no-op, no log

    session.add(
        ModelVisibilityLog(
            model_version_id=mv.id,
            from_visibility=mv.visibility,
            to_visibility=body.visibility,
            actor_id=user.id,
            comment=body.comment,
        )
    )
    mv.visibility = body.visibility
    await session.commit()
    await session.refresh(mv)
    return _model_version_to_read(
        mv, owner, name, rm.detector_id, detector_version_tag, dv_status
    )


@router.patch("/{owner}/{name}/owner", response_model=RegisteredModelRead)
async def transfer_owner(
    owner: str,
    name: str,
    body: OwnerTransferRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow)],
) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)

    new_owner = (
        await session.execute(select(User).where(User.handle == body.new_owner_handle))
    ).scalar_one_or_none()
    if new_owner is None:
        raise HTTPException(422, f"user '{body.new_owner_handle}' not found")
    if new_owner.id == rm.owner_id:
        raise HTTPException(422, "new owner is current owner")

    # Collision check: target user already owns a model for the same detector?
    collision = (
        await session.execute(
            select(RegisteredModel).where(
                RegisteredModel.owner_id == new_owner.id,
                RegisteredModel.detector_id == rm.detector_id,
            )
        )
    ).scalar_one_or_none()
    if collision is not None:
        raise HTTPException(
            409,
            f"'{body.new_owner_handle}' already owns a model for this detector",
        )

    old_owner_id = rm.owner_id

    # Capture handle + detector name explicitly to avoid async lazy-load on relationship.
    old_owner_handle = (
        await session.execute(select(User.handle).where(User.id == rm.owner_id))
    ).scalar_one()
    detector_name = (
        await session.execute(
            select(Detector.name).where(Detector.id == rm.detector_id)
        )
    ).scalar_one()
    old_mlflow_name = f"{old_owner_handle}/{detector_name}"
    new_mlflow_name = f"{new_owner.handle}/{detector_name}"

    rm.owner_id = new_owner.id
    await client.rename_registered_model(old_mlflow_name, new_mlflow_name)

    session.add(
        ModelOwnerTransferLog(
            registered_model_id=rm.id,
            from_owner_id=old_owner_id,
            to_owner_id=new_owner.id,
            actor_id=user.id,
            comment=body.comment,
        )
    )
    await session.commit()
    await session.refresh(rm)
    summary = (await session.execute(_summary_query_for_rm(rm.id, user))).one()
    return RegisteredModelRead(
        owner=new_owner.handle,
        name=name,
        description=rm.description,
        tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )


@router.delete("/{owner}/{name}", status_code=204)
async def delete_model(
    owner: str,
    name: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow)],
) -> None:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    # Capture mlflow_name BEFORE deletion to avoid lazy-load issues post-delete.
    detector_name = (
        await session.execute(
            select(Detector.name).where(Detector.id == rm.detector_id)
        )
    ).scalar_one()
    owner_handle = (
        await session.execute(select(User.handle).where(User.id == rm.owner_id))
    ).scalar_one()
    mlflow_name = f"{owner_handle}/{detector_name}"

    await client.delete_registered_model(mlflow_name)
    # Explicitly delete child ModelVersion rows first so that the DELETE is
    # portable across backends: PostgreSQL relies on ondelete=CASCADE (FK-level),
    # while SQLite in tests may have FK enforcement disabled.
    await session.execute(
        sa.delete(ModelVersion).where(ModelVersion.registered_model_id == rm.id)
    )
    await session.delete(rm)
    await session.commit()


@router.delete("/{owner}/{name}/versions/{version}", status_code=204)
async def delete_model_version_namespaced(
    owner: str,
    name: str,
    version: int,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    client: Annotated[MlflowClient, Depends(get_mlflow)],
) -> None:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    mv = (
        await session.execute(
            select(ModelVersion).where(
                ModelVersion.registered_model_id == rm.id,
                ModelVersion.mlflow_version == version,
            )
        )
    ).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")

    detector_name = (
        await session.execute(
            select(Detector.name).where(Detector.id == rm.detector_id)
        )
    ).scalar_one()
    owner_handle = (
        await session.execute(select(User.handle).where(User.id == rm.owner_id))
    ).scalar_one()
    mlflow_name = f"{owner_handle}/{detector_name}"

    await client.delete_model_version(mlflow_name, str(version))
    await session.delete(mv)  # cascade ModelVisibilityLog via ondelete
    await session.commit()


@router.patch("/{owner}/{name}", response_model=RegisteredModelRead)
async def update_model(
    owner: str,
    name: str,
    body: RegisteredModelUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> RegisteredModelRead:
    rm = await resolve_registered_model(owner, name, session, user, write=True)
    if body.description is not None:
        rm.description = body.description
    if body.tags is not None:
        # Pydantic dict[str, str] schema validates value types;
        # defensive check as belt-and-suspenders guard
        for k, v in body.tags.items():
            if not isinstance(v, str):
                raise HTTPException(422, f"tag value for '{k}' must be string")
        rm.tags = body.tags
    await session.commit()
    await session.refresh(rm)
    summary = (await session.execute(_summary_query_for_rm(rm.id, user))).one()
    return RegisteredModelRead(
        owner=owner,
        name=name,
        description=rm.description,
        tags=rm.tags,
        latest_version=summary.latest_version,
        latest_production_version=summary.latest_production_version,
        latest_staging_version=summary.latest_staging_version,
        created_at=rm.created_at,
    )
