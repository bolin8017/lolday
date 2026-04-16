import uuid
from datetime import datetime

from fastapi_users import schemas

from app.models import Role


class UserRead(schemas.BaseUser[uuid.UUID]):
    role: Role
    display_name: str | None = None
    created_at: datetime | None = None


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None


class AdminUserUpdate(schemas.BaseUserUpdate):
    role: Role | None = None
    display_name: str | None = None
