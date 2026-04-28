import enum
from datetime import datetime

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"


# cf_access.py synthesises ``service-<common_name>@cf-access.local`` for
# JWTs that carry only ``common_name`` (CF Access service-token principals).
SERVICE_TOKEN_EMAIL_DOMAIN = "@cf-access.local"
SERVICE_TOKEN_DISPLAY_NAME = "Internal service token"


class User(SQLAlchemyBaseUserTableUUID, Base):
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="role_enum"), default=Role.USER, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    discord_user_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def is_service_token(self) -> bool:
        """True for CF Access service-token principals.

        Phase 12.1 skips Discord notifications for them — those events
        flooded the user-event channel with un-actionable noise.
        """
        return bool(self.email and self.email.endswith(SERVICE_TOKEN_EMAIL_DOMAIN))
