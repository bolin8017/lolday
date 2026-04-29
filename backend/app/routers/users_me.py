"""Custom /users/me router (Phase 10).

Replaces fastapi-users' get_users_router so that /me endpoints authenticate
via Cloudflare Access SSO (cf_access_user) instead of a password-bearer JWT.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models import User
from app.schemas.user import UserRead, UserSelfUpdate
from app.users import current_active_user

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_me(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    return user


# Belt-and-suspenders defence: UserSelfUpdate's `extra='forbid'` is the
# primary schema-level guard, but the handler also rejects any field outside
# this set so a future schema regression that accidentally widened the
# schema (e.g. exposing `role` or `email`) cannot silently mutate the ORM row.
_ALLOWED_SELF_FIELDS = frozenset({"display_name", "discord_user_id"})


@router.patch("/me", response_model=UserRead)
async def update_me(
    body: UserSelfUpdate,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field not in _ALLOWED_SELF_FIELDS:
            raise HTTPException(
                status_code=500,
                detail=f"schema drift: unexpected field {field!r} in UserSelfUpdate",
            )
        setattr(user, field, value)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
