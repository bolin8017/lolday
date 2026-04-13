from fastapi import APIRouter, Depends
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
