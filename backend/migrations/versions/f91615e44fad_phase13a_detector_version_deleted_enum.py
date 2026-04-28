"""phase13a detector version deleted enum

Add ``deleted`` to the ``detector_version_status`` enum for user-initiated
soft deletes (Phase 13a A4). Distinct from ``retention_pruned`` which is
set by the reconciler GC.

SQLite (used by unit tests) stores enums as VARCHAR with a CHECK
constraint and does not need an ALTER TYPE — the parity test reads
allowed values off the Python enum directly, so no SQLite-specific work
is required here.

Revision ID: f91615e44fad
Revises: f9a2c4e8b01a
Create Date: 2026-04-28 14:26:31.786853

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "f91615e44fad"
down_revision: Union[str, Sequence[str], None] = "f9a2c4e8b01a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL ALTER TYPE ADD VALUE cannot run inside a transaction.
        # Commit the current transaction first, then issue the DDL in
        # autocommit mode. IF NOT EXISTS keeps re-runs idempotent (PG 9.3+).
        op.execute(text("COMMIT"))
        op.execute(
            text("ALTER TYPE detector_version_status ADD VALUE IF NOT EXISTS 'deleted'")
        )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the
    # type. Phase 13a accepts forward-only per spec authorization. Existing
    # rows with status='deleted' would block any type recreation, so even the
    # heroic recreate-and-rename approach is unsafe in practice.
    pass
