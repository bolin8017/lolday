import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_role
from app.models import Role, User
from app.schemas import UserRead

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/users", response_model=list[UserRead])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    _user: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(select(User).offset(skip).limit(limit))
    return result.scalars().all()


class AdminUserUpdate(BaseModel):
    """Mutable fields an admin may change on another user. `extra='forbid'`
    so a caller cannot smuggle `is_superuser`/`email`/etc. through the body.
    All fields optional so PATCH bodies only send what changes."""
    model_config = {"extra": "forbid"}
    role: Role | None = None


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    admin: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Promote/demote another user's role (or self).

    Last-admin safeguard: the user table must always contain ≥1 admin, so
    any PATCH that would leave zero admins (whether self-demote of the sole
    admin, or another admin demoting the last remaining admin from a
    two-admin racey state) is rejected with 400. Self-demote when another
    admin exists is permitted.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        return target

    new_role = changes.get("role")
    if (
        target.role == Role.ADMIN
        and new_role is not None
        and new_role != Role.ADMIN
    ):
        other_admins = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == Role.ADMIN, User.id != target.id)
            )
        ).scalar_one()
        if other_admins == 0:
            raise HTTPException(
                status_code=400,
                detail="cannot demote the last remaining admin",
            )

    old_role = target.role
    for field, value in changes.items():
        setattr(target, field, value)
    session.add(target)
    await session.commit()
    await session.refresh(target)
    if new_role is not None and new_role != old_role:
        logger.info(
            "admin role change: actor=%s target=%s old=%s new=%s",
            admin.email, target.email, old_role.value, target.role.value,
        )
    return target
