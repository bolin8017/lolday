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
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

# Use JSONB on PostgreSQL; fall back to plain JSON on SQLite (tests).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class DetectorVersionStatus(StrEnum):
    ACTIVE = "active"
    RETENTION_PRUNED = "retention_pruned"  # GC by reconciler retention
    DELETED = "deleted"  # Phase 13a (A4): user-initiated soft delete


class DetectorBuildStatus(StrEnum):
    PENDING = "pending"
    CLONING = "cloning"
    VALIDATING = "validating"
    BUILDING = "building"
    SCANNING = "scanning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CVE_BLOCKED = "cve_blocked"


class Detector(Base):
    __tablename__ = "detector"
    __table_args__ = (
        Index(
            "detector_owner_git_unique",
            "owner_id",
            "git_url",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    git_url: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DetectorVersion(Base):
    __tablename__ = "detector_version"
    __table_args__ = (
        UniqueConstraint("detector_id", "git_tag", name="detector_version_tag_unique"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("detector.id", ondelete="CASCADE"),
        nullable=False,
    )
    git_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    harbor_image: Mapped[str] = mapped_column(String(500), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    mlflow_experiment_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[DetectorVersionStatus] = mapped_column(
        SAEnum(
            DetectorVersionStatus,
            name="detector_version_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=DetectorVersionStatus.ACTIVE,
        nullable=False,
    )


class DetectorBuild(Base):
    __tablename__ = "detector_build"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("detector.id", ondelete="CASCADE"),
        nullable=False,
    )
    git_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    triggered_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    k8s_job_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[DetectorBuildStatus] = mapped_column(
        SAEnum(
            DetectorBuildStatus,
            name="detector_build_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=DetectorBuildStatus.PENDING,
        nullable=False,
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    log_tail: Mapped[str | None] = mapped_column(Text)
    trivy_critical: Mapped[int | None] = mapped_column(Integer)
    trivy_high: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
