import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import DatasetConfig, Job, User
from app.models.dataset import DatasetVisibility
from app.models.job import NON_TERMINAL_STATUSES
from app.schemas.dataset import (
    DatasetConfigCreate,
    DatasetConfigList,
    DatasetConfigRead,
    DatasetConfigUpdate,
)
from app.services.dataset import DatasetValidationError, parse_csv
from app.users import current_active_user

router = APIRouter()


async def _get_readable_dataset(
    ds_id: uuid.UUID, session: AsyncSession, user: User
) -> DatasetConfig:
    ds = await session.get(DatasetConfig, ds_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if (
        ds.visibility == DatasetVisibility.PRIVATE
        and ds.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise HTTPException(status_code=404, detail="dataset not found")
    return ds


async def _get_writable_dataset(
    ds_id: uuid.UUID, session: AsyncSession, user: User
) -> DatasetConfig:
    ds = await _get_readable_dataset(ds_id, session, user)
    if ds.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    return ds


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DatasetConfigRead)
async def create_dataset(
    body: DatasetConfigCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    if len(body.csv_content.encode("utf-8")) > settings.DATASET_CSV_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds {settings.DATASET_CSV_MAX_BYTES} bytes",
        )

    try:
        parsed = parse_csv(body.csv_content)
    except DatasetValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    stmt = select(DatasetConfig).where(
        DatasetConfig.owner_id == user.id,
        DatasetConfig.name == body.name,
        DatasetConfig.deleted_at.is_(None),
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"dataset name '{body.name}' already in use"
        )

    ds = DatasetConfig(
        name=body.name,
        description=body.description,
        owner_id=user.id,
        visibility=body.visibility,
        csv_content=body.csv_content,
        csv_checksum=parsed.checksum,
        sample_count=parsed.sample_count,
        label_distribution=parsed.label_distribution,
        family_distribution=parsed.family_distribution,
        size_bytes=parsed.size_bytes,
    )
    session.add(ds)
    await session.commit()
    await session.refresh(ds)
    return DatasetConfigRead.model_validate(ds)


@router.get("", response_model=DatasetConfigList)
async def list_datasets(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    owner_id: uuid.UUID | None = None,
    visibility: DatasetVisibility | None = None,
    search: str | None = None,
) -> DatasetConfigList:
    filters = [DatasetConfig.deleted_at.is_(None)]

    if user.role.value != "admin":
        filters.append(
            or_(
                DatasetConfig.visibility == DatasetVisibility.PUBLIC,
                DatasetConfig.owner_id == user.id,
            )
        )

    if owner_id is not None:
        filters.append(DatasetConfig.owner_id == owner_id)
    if visibility is not None:
        filters.append(DatasetConfig.visibility == visibility)
    if search:
        filters.append(DatasetConfig.name.ilike(f"%{search}%"))

    count_stmt = select(func.count()).select_from(DatasetConfig).where(and_(*filters))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        select(DatasetConfig)
        .where(and_(*filters))
        .order_by(DatasetConfig.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await session.execute(stmt)).scalars().all()

    return DatasetConfigList(
        items=[DatasetConfigRead.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{ds_id}", response_model=DatasetConfigRead)
async def get_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    ds = await _get_readable_dataset(ds_id, session, user)
    return DatasetConfigRead.model_validate(ds)


@router.get("/{ds_id}/csv")
async def get_dataset_csv(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    ds = await _get_readable_dataset(ds_id, session, user)
    return Response(
        content=ds.csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{ds.name}.csv"'},
    )


@router.patch("/{ds_id}", response_model=DatasetConfigRead)
async def update_dataset(
    ds_id: uuid.UUID,
    body: DatasetConfigUpdate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    ds = await _get_writable_dataset(ds_id, session, user)

    if body.name is not None and body.name != ds.name:
        stmt = select(DatasetConfig).where(
            DatasetConfig.owner_id == ds.owner_id,
            DatasetConfig.name == body.name,
            DatasetConfig.deleted_at.is_(None),
            DatasetConfig.id != ds.id,
        )
        if (await session.execute(stmt)).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="name in use")
        ds.name = body.name
    if body.description is not None:
        ds.description = body.description
    if body.visibility is not None:
        ds.visibility = body.visibility

    await session.commit()
    await session.refresh(ds)
    return DatasetConfigRead.model_validate(ds)


@router.post(
    "/{ds_id}/clone",
    status_code=status.HTTP_201_CREATED,
    response_model=DatasetConfigRead,
)
async def clone_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> DatasetConfigRead:
    orig = await _get_readable_dataset(ds_id, session, user)

    base = f"{orig.name}-clone"
    new_name = base
    suffix = 2
    while True:
        stmt = select(DatasetConfig).where(
            DatasetConfig.owner_id == user.id,
            DatasetConfig.name == new_name,
            DatasetConfig.deleted_at.is_(None),
        )
        if (await session.execute(stmt)).scalar_one_or_none() is None:
            break
        new_name = f"{base}-{suffix}"
        suffix += 1

    copy = DatasetConfig(
        name=new_name,
        description=orig.description,
        owner_id=user.id,
        visibility=DatasetVisibility.PUBLIC,
        csv_content=orig.csv_content,
        csv_checksum=orig.csv_checksum,
        sample_count=orig.sample_count,
        label_distribution=orig.label_distribution,
        family_distribution=orig.family_distribution,
        size_bytes=orig.size_bytes,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return DatasetConfigRead.model_validate(copy)


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    ds = await _get_writable_dataset(ds_id, session, user)

    stmt = (
        select(func.count())
        .select_from(Job)
        .where(
            Job.status.in_(NON_TERMINAL_STATUSES),
            or_(
                Job.train_dataset_id == ds.id,
                Job.test_dataset_id == ds.id,
                Job.predict_dataset_id == ds.id,
            ),
        )
    )
    in_flight = (await session.execute(stmt)).scalar_one()
    if in_flight > 0:
        raise HTTPException(
            status_code=409,
            detail=f"{in_flight} in-flight job(s) reference this dataset",
        )

    ds.deleted_at = datetime.now(UTC)
    await session.commit()
    return Response(status_code=204)
