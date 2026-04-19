import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# JSONB on PostgreSQL; falls back to plain JSON on SQLite (tests).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class DatasetVisibility(str, enum.Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class DatasetConfig(Base):
    __tablename__ = "dataset_config"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    visibility: Mapped[DatasetVisibility] = mapped_column(
        SAEnum(DatasetVisibility, name="dataset_visibility_enum",
               values_callable=lambda x: [e.value for e in x]),
        default=DatasetVisibility.PUBLIC,
        nullable=False,
    )
    csv_content: Mapped[str] = mapped_column(Text, nullable=False)
    csv_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_count: Mapped[int] = mapped_column(nullable=False)
    label_distribution: Mapped[dict] = mapped_column(_JSONB, default=dict)
    family_distribution: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_dataset_config_owner_name_unique",
            "owner_id",
            "name",
            unique=True,
            postgresql_where="deleted_at IS NULL",
            sqlite_where=None,
        ),
        Index("ix_dataset_config_owner", "owner_id"),
        Index("ix_dataset_config_visibility", "visibility"),
    )
