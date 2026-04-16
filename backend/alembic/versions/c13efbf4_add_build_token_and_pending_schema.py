"""add build_token and pending_schema

Revision ID: c13efbf4
Revises: f5c431c00187
Create Date: 2026-04-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c13efbf4'
down_revision: Union[str, Sequence[str], None] = 'f5c431c00187'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add build_token and pending_schema columns to detector_build."""
    op.add_column(
        'detector_build',
        sa.Column('build_token', sa.String(length=80), nullable=True),
    )
    op.add_column(
        'detector_build',
        sa.Column(
            'pending_schema',
            postgresql.JSONB(astext_type=Text()).with_variant(sa.JSON(), 'sqlite'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove build_token and pending_schema columns from detector_build."""
    op.drop_column('detector_build', 'pending_schema')
    op.drop_column('detector_build', 'build_token')
