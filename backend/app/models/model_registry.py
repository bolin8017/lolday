import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ModelVersionStage(str, enum.Enum):
    """Mirrors MLflow stages; 'none' = unassigned."""

    NONE = "None"
    STAGING = "Staging"
    PRODUCTION = "Production"
    ARCHIVED = "Archived"


class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    mlflow_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mlflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    current_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum",
               values_callable=lambda x: [e.value for e in x]),
        default=ModelVersionStage.NONE,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    source_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job.id"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_model_version_name_version_unique",
            "mlflow_name",
            "mlflow_version",
            unique=True,
        ),
        Index("ix_model_version_owner", "owner_id"),
        Index("ix_model_version_stage", "current_stage"),
    )


class ModelTransitionLog(Base):
    __tablename__ = "model_transition_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id"), nullable=False
    )
    from_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum",
               values_callable=lambda x: [e.value for e in x],
               create_type=False),
        nullable=False,
    )
    to_stage: Mapped[ModelVersionStage] = mapped_column(
        SAEnum(ModelVersionStage, name="model_stage_enum",
               values_callable=lambda x: [e.value for e in x],
               create_type=False),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_model_transition_version", "model_version_id"),
    )
