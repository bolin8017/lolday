"""Audit log — append-only record of security-relevant actions.

Per spec 2026-05-12-security-hardening-design.md §6.5 (M-audit-log) and
plan 2026-05-14-security-hardening-p5-audit-observability.md design
decision D1, payloads in before_jsonb / after_jsonb are intentionally
cherry-picked per call-site rather than full ORM row dumps:
schema-coupling avoidance + PII control + bounded storage.

The table is append-only: there is no UPDATE or DELETE path in the
codebase. Operators who need to redact a row (e.g. GDPR right-to-be-
forgotten) do so out-of-band via psql.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, User

# JSONB on PostgreSQL (prod), plain JSON on SQLite (test).
# Postgres-first form matches the other 5 models in this package
# (dataset.py, detector.py, job.py, job_event.py, model_registry.py).
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    before_jsonb: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    after_jsonb: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped[User] = relationship(foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_audit_log_target_ts", "target_type", "target_id", "ts"),
        Index("ix_audit_log_actor_ts", "actor_id", "ts"),
    )
