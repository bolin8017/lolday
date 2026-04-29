"""drop fastapi-users vestige columns from user table

Phase 10 migrated to Cloudflare Access SSO. The four columns inherited from
fastapi-users-db-sqlalchemy (hashed_password, is_active, is_superuser,
is_verified) have been written-but-never-read since. Resolves
docs/architecture.md §9 #7.

Revision ID: d8928ee4a13d
Revises: f37230063a20
Create Date: 2026-04-29 12:11:29.710495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8928ee4a13d'
down_revision: Union[str, Sequence[str], None] = 'f37230063a20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("is_active")
        batch_op.drop_column("is_superuser")
        batch_op.drop_column("is_verified")


def downgrade() -> None:
    """Local-dev rollback only — repo policy forbids prod downgrades
    (.claude/rules/alembic-migrations.md). Columns restored as nullable;
    original constant values (hashed_password sentinel, is_active=true,
    is_verified=true, is_superuser=false) are not backfilled."""
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("hashed_password", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_superuser", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_verified", sa.Boolean(), nullable=True))
