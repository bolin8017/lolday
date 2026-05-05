import uuid
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# JSONB on PostgreSQL; falls back to plain JSON on SQLite (tests).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class JobType(StrEnum):
    TRAIN = "train"
    EVALUATE = "evaluate"
    PREDICT = "predict"


class JobStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


NON_TERMINAL_STATUSES = frozenset(
    {
        JobStatus.PENDING,
        JobStatus.PREPARING,
        JobStatus.RUNNING,
    }
)


class ResourceProfile(StrEnum):
    STANDARD = "standard"
    GPU1 = "gpu1"
    GPU2 = "gpu2"

    @property
    def gpu_count(self) -> int:
        """GPUs allocated by build_volcano_job_manifest for this profile.

        Carried on the enum (not a module-level dict) so a new enum value
        added without the corresponding allocation entry fails loudly at
        import time rather than with a KeyError on the first reconcile
        tick against a persisted Job row.
        """
        return _RESOURCE_PROFILE_GPU_COUNT[self]


# Kept as a frozen module-level constant (via MappingProxyType) instead of
# a plain dict so accidental `RESOURCE_PROFILE_GPU_COUNT[x] = 99` at
# runtime raises TypeError. Verified total against the enum below.
_RESOURCE_PROFILE_GPU_COUNT: "MappingProxyType[ResourceProfile, int]" = (
    MappingProxyType(
        {
            ResourceProfile.STANDARD: 0,
            ResourceProfile.GPU1: 1,
            ResourceProfile.GPU2: 2,
        }
    )
)
assert set(_RESOURCE_PROFILE_GPU_COUNT.keys()) == set(ResourceProfile), (
    "RESOURCE_PROFILE map not total over ResourceProfile — adding an enum "
    "value without updating the map would silently break scheduling."
)

# Public alias preserves the Phase 8 import site (services/job_spec.py).
RESOURCE_PROFILE_GPU_COUNT = _RESOURCE_PROFILE_GPU_COUNT


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[JobType] = mapped_column(
        SAEnum(
            JobType,
            name="job_type_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(
            JobStatus,
            name="job_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=JobStatus.PENDING,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector_version.id"), nullable=False
    )
    train_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    test_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    predict_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_config.id"), nullable=True
    )
    source_model_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_version.id"), nullable=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    resolved_config: Mapped[dict] = mapped_column(_JSONB, nullable=False)
    # Phase 13b B3: raw user-submitted params (before defaults merge), used
    # by the resolved-config UI to highlight what the user actually changed.
    user_params: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    mlflow_experiment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    k8s_job_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_metrics: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    resource_profile: Mapped[ResourceProfile] = mapped_column(
        SAEnum(
            ResourceProfile,
            name="resource_profile_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ResourceProfile.STANDARD,
        nullable=False,
    )
    # Phase 5 — optional per-job active deadline override. None falls
    # back to the per-type default in config.JOB_ACTIVE_DEADLINE_*_SECONDS;
    # caps validated by JobCreate against JOB_ACTIVE_DEADLINE_*_MAX_SECONDS.
    active_deadline_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_job_owner_submitted", "owner_id", "submitted_at"),
        Index(
            "ix_job_in_flight",
            "status",
            postgresql_where=(
                "status IN ('pending'::job_status_enum,"
                " 'preparing'::job_status_enum,"
                " 'running'::job_status_enum)"
            ),
            sqlite_where=None,
        ),
        Index("ix_job_detector_version", "detector_version_id"),
        Index("ix_job_idempotency", "idempotency_key", "submitted_at"),
    )
