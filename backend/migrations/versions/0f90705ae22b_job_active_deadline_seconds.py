"""job_active_deadline_seconds

Revision ID: 0f90705ae22b
Revises: f1e8115c3234
Create Date: 2026-05-05 12:40:45.581049

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f90705ae22b"
down_revision: Union[str, Sequence[str], None] = "f1e8115c3234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Phase 5 — per-job active_deadline_seconds override.

    Nullable column, no backfill. Old jobs keep activeDeadlineSeconds
    derived from the per-type default (config.JOB_ACTIVE_DEADLINE_*_SECONDS);
    new jobs may opt into a per-job override capped by
    config.JOB_ACTIVE_DEADLINE_*_MAX_SECONDS.
    """
    op.add_column(
        "job",
        sa.Column("active_deadline_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job", "active_deadline_seconds")
