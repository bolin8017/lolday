"""phase13b job user_params column

Add nullable JSONB column ``Job.user_params`` storing the raw user-submitted
params before defaults merge. Used by Phase 13b's ResolvedConfigCard UI to
show "your params" separately from the merged ``resolved_config`` and
highlight what the user actually changed.

NULL is the legacy-row sentinel — jobs submitted before Phase 13b B3 never
captured this and won't be backfilled.

Revision ID: f37230063a20
Revises: a4b8e7c91d52
Create Date: 2026-04-28 20:52:53.881885

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f37230063a20'
down_revision: Union[str, Sequence[str], None] = 'a4b8e7c91d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "job",
        sa.Column(
            "user_params",
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("job", "user_params")
