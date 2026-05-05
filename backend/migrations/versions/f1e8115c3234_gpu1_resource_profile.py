"""gpu1_resource_profile

Revision ID: f1e8115c3234
Revises: d8928ee4a13d
Create Date: 2026-05-05 12:32:13.460612

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1e8115c3234"
down_revision: Union[str, Sequence[str], None] = "d8928ee4a13d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'gpu1' to resource_profile_enum BEFORE 'gpu2'.

    Postgres ALTER TYPE ADD VALUE cannot run inside a transaction block,
    so wrap in autocommit_block. SQLite (tests) has no named enum types —
    no-op there. Adding BEFORE 'gpu2' keeps the display order natural in
    any UI that ORDERs by enum position.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE resource_profile_enum "
                "ADD VALUE IF NOT EXISTS 'gpu1' BEFORE 'gpu2'"
            )


def downgrade() -> None:
    """Postgres does not support removing enum values. Downgrade is a no-op.

    Rolling back while 'gpu1' is referenced by job rows is unsafe — callers
    must manually migrate those rows to 'standard' or 'gpu2' first.
    """
