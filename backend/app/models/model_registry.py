import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, User

# JSONB on PostgreSQL, plain JSON on SQLite (test).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class ModelVersionStage(StrEnum):
    NONE = "None"
    STAGING = "Staging"
    PRODUCTION = "Production"
    ARCHIVED = "Archived"


class ModelVersionVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class RegisteredModel(Base):
    __tablename__ = "registered_model"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[dict[str, str]] = mapped_column(_JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    detector: Mapped["Detector"] = relationship()  # type: ignore[name-defined]  # noqa: F821  # forward ref to backend/app/models/detector.py to avoid cyclic import

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "detector_id", name="uq_registered_model_owner_detector"
        ),
        Index("ix_registered_model_owner", "owner_id"),
        Index("ix_registered_model_detector", "detector_id"),
    )

    @property
    def mlflow_name(self) -> str:
        """`{handle}/{detector.name}` — derived, never stored."""
        return f"{self.owner.handle}/{self.detector.name}"


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    mlflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    current_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelVersionStage.NONE,
        nullable=False,
    )
    visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelVersionVisibility.PRIVATE,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    source_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    registered_model: Mapped[RegisteredModel] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "registered_model_id",
            "mlflow_version",
            name="uq_model_version_per_registered",
        ),
        Index("ix_model_version_registered_model", "registered_model_id"),
        Index("ix_model_version_owner", "owner_id"),
        Index("ix_model_version_stage", "current_stage"),
        Index("ix_model_version_visibility", "visibility"),
    )


class ModelTransitionLog(Base):
    """Existing audit table — schema unchanged."""

    __tablename__ = "model_transition_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False
    )
    from_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    to_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(
            ModelVersionStage,
            name="model_stage_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_model_transition_version", "model_version_id"),)


class ModelVisibilityLog(Base):
    __tablename__ = "model_visibility_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False
    )
    from_visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    to_visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_model_visibility_log_version", "model_version_id"),)


class ModelOwnerTransferLog(Base):
    __tablename__ = "model_owner_transfer_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    from_owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    to_owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_model_owner_transfer_log_model", "registered_model_id"),
    )
