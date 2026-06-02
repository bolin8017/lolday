"""add ix_audit_log_ts

Revision ID: 8430fd95c5e2
Revises: 3a4b5c6d7e8f
Create Date: 2026-06-02 12:26:48.295051

Standalone index on ``audit_log.ts`` so the audit-log retention sweep
(``app/reconciler/audit_retention.py``) can range-delete ``WHERE ts < cutoff``
via an index scan instead of a seqscan as the table grows. The two existing
composite indexes (ix_audit_log_target_ts, ix_audit_log_actor_ts) lead with
non-ts columns, so neither serves a ts-only range predicate.

Spec: docs/superpowers/specs/2026-06-02-audit-log-retention-design.md
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8430fd95c5e2"
down_revision: str | Sequence[str] | None = "3a4b5c6d7e8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_ts", table_name="audit_log")
