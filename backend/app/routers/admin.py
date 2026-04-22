from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_role
from app.models import Role, User
from app.schemas import UserRead

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


class AdminUserRolePatch(BaseModel):
    model_config = {"extra": "forbid"}
    role: Role


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user_role(
    user_id: UUID,
    body: AdminUserRolePatch,
    admin: User = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Promote/demote another user's role.

    Admins cannot demote their own account — prevents accidental lockouts
    where the only remaining admin drops themselves to USER.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="user not found")
    if target.id == admin.id and body.role != Role.ADMIN:
        raise HTTPException(
            status_code=400,
            detail="admins cannot demote their own account",
        )
    target.role = body.role
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target
