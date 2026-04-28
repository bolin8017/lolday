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
    # Machine principal — set on rows created from a Cloudflare Access
    # service-token JWT (synthesised email ``service-<cn>@cf-access.local``).
    # Discord notification policy keys off ``Role.SERVICE_TOKEN`` so the
    # rule survives the operator editing a row's email by hand.
    SERVICE_TOKEN = "service_token"


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

        Backed by ``role``, not by an email-suffix probe — survives an
        admin editing the email field, surfaces in /admin/users as a
        normal column, and is indexable via the existing ``role_enum``.
        """
        return self.role == Role.SERVICE_TOKEN
