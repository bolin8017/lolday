"""phase8_gpu2_profile

Revision ID: 8a1c2d4e5f60
Revises: d3f179666394
Create Date: 2026-04-21 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "8a1c2d4e5f60"
down_revision: Union[str, Sequence[str], None] = "d3f179666394"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'gpu2' to resource_profile_enum.

    Postgres does not allow ALTER TYPE ADD VALUE inside a transaction block,
    so commit the surrounding transaction first. SQLite (tests) does not have
    named enum types — the ALTER is a no-op there.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE resource_profile_enum ADD VALUE IF NOT EXISTS 'gpu2'")


def downgrade() -> None:
    """Postgres does not support removing enum values. Downgrade is a no-op.

    Rolling back to Phase 7.5 while 'gpu2' is still referenced by job rows is
    unsafe — callers must manually migrate those rows to 'standard' first.
    """
