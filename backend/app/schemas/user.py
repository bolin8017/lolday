import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import Role

# Discord snowflakes are 64-bit IDs serialised as decimal strings, today
# 17-19 digits with legacy and future IDs bracketing 15-20.
_DISCORD_ID_RE = re.compile(r"^\d{15,20}$")


def _validate_discord_user_id(v):
    """Allow None, coerce empty string → None, else require 15-20 digits."""
    if v is None or v == "":
        return None
    if not _DISCORD_ID_RE.match(v):
        raise ValueError(
            "discord_user_id must be 15-20 digits (copy from Discord "
            "with Developer Mode enabled → right-click → Copy User ID)"
        )
    return v


class UserRead(BaseModel):
    # email stays as plain ``str``: Cloudflare Access service tokens
    # synthesize ``service-<name>@cf-access.local``, and Pydantic's
    # ``EmailStr`` rejects ``.local`` (RFC 6761 reserved TLD). Email
    # format is validated on user creation; the response shape doesn't
    # need to re-validate.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    handle: str
    role: Role
    display_name: str | None = None
    discord_user_id: str | None = None
    created_at: datetime | None = None


class UserSelfUpdate(BaseModel):
    """Body accepted by ``PATCH /users/me`` — only self-mutable fields.

    ``extra="forbid"`` means sending ``role``, ``email``, etc. returns 422
    rather than silently dropping them. This is the sole line between a
    regular user and privilege escalation through ``/users/me``; see
    ``tests/test_user_discord_id.py::test_patch_users_me_rejects_role_smuggling``.
    """

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    discord_user_id: str | None = None

    _validate_discord_self = field_validator("discord_user_id", mode="before")(
        _validate_discord_user_id
    )
