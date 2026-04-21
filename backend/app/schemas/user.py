import re
import uuid
from datetime import datetime

from fastapi_users import schemas
from pydantic import field_validator

from app.models import Role


# Discord snowflakes are 64-bit IDs serialised as decimal strings, today
# 17–19 digits with legacy and future IDs bracketing 15–20.
_DISCORD_ID_RE = re.compile(r"^\d{15,20}$")


def _validate_discord_user_id(v):
    """Allow None, coerce empty string → None, else require 15–20 digits."""
    if v is None or v == "":
        return None
    if not _DISCORD_ID_RE.match(v):
        raise ValueError(
            "discord_user_id must be 15–20 digits (copy from Discord "
            "with Developer Mode enabled → right-click → Copy User ID)"
        )
    return v


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role
    display_name: str | None = None
    discord_user_id: str | None = None
    created_at: datetime | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
    discord_user_id: str | None = None

    _validate_discord = field_validator("discord_user_id", mode="before")(
        _validate_discord_user_id
    )


class AdminUserUpdate(schemas.BaseUserUpdate):
    role: Role | None = None
    display_name: str | None = None
    discord_user_id: str | None = None

    _validate_discord = field_validator("discord_user_id", mode="before")(
        _validate_discord_user_id
    )
