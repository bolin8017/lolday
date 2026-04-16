import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class GitProvider(str, enum.Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


class UserGitCredential(Base):
    __tablename__ = "user_git_credential"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[GitProvider] = mapped_column(
        SAEnum(GitProvider, name="git_provider",
               values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    encrypted_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
