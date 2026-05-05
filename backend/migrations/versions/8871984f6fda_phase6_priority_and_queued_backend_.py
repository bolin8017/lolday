"""phase6 priority and queued_backend status

Add `priority INTEGER NOT NULL DEFAULT 0` column and index on the `job`
table, and extend the `job_status_enum` Postgres native enum with the
new `queued_backend` value used by the Phase 6 backend-layer FIFO
scheduler (spec §6.3–§6.4).

Postgres notes:
- `ALTER TYPE … ADD VALUE` cannot run inside a transaction block. The
  operation is wrapped in `autocommit_block()` — the same pattern used
  by the Phase 3 GPU1 migration (`f1e8115c3234`).
- Enum values cannot be dropped once added; downgrade intentionally
  omits the reverse step (accepted limitation per plan §B.3).

SQLite notes (tests):
- SQLite stores enums as plain strings; the column has no named enum
  type. The ALTER TYPE step is skipped for SQLite — Task C's Python
  StrEnum addition is the only change needed there.

Revision ID: 8871984f6fda
Revises: 0f90705ae22b
Create Date: 2026-05-05 16:15:45.553447

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8871984f6fda"
down_revision: Union[str, Sequence[str], None] = "0f90705ae22b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 6 — priority column + queued_backend enum value.

    1. Add `priority INTEGER NOT NULL DEFAULT 0` to the `job` table.
    2. Index it for fast FIFO-sorted queries.
    3. Extend `job_status_enum` with `queued_backend` (Postgres only;
       SQLite has no named enum type, so the Python StrEnum change in
       Task C is sufficient there).
    """
    op.add_column(
        "job",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index("ix_job_priority", "job", ["priority"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE job_status_enum "
                "ADD VALUE IF NOT EXISTS 'queued_backend'"
            )


def downgrade() -> None:
    """Drop the priority index and column.

    The `queued_backend` enum value is intentionally NOT removed: Postgres
    does not support `ALTER TYPE … DROP VALUE` without full table rewrite,
    and leaving an unused enum value is harmless. Any row with
    status='queued_backend' must be migrated before downgrading.
    """
    op.drop_index("ix_job_priority", table_name="job")
    op.drop_column("job", "priority")
