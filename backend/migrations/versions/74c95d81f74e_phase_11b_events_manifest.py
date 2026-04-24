"""phase 11b events + manifest

Revision ID: 74c95d81f74e
Revises: b2e7c8a1f330
Create Date: 2026-04-24 16:41:19.466013

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '74c95d81f74e'
down_revision: Union[str, Sequence[str], None] = 'b2e7c8a1f330'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add manifest column to detector_version
    op.add_column('detector_version', sa.Column('manifest', postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True))

    # Create job_events table for structured per-job event log
    op.create_table(
        "job_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), sa.ForeignKey("job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_events_job_ts",
        "job_events",
        ["job_id", "ts"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_events_job_ts", table_name="job_events")
    op.drop_table("job_events")
    op.drop_column('detector_version', 'manifest')
