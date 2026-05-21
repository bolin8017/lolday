"""unify UUID column types to sa.Uuid()

Revision ID: 3a4b5c6d7e8f
Revises: 90125ce5ad35
Create Date: 2026-05-21 12:55:00.000000

Spec: docs/superpowers/specs/2026-05-21-uuid-type-consistency-design.md
Issue: https://github.com/bolin8017/lolday/issues/530

Standardise the seven UUID columns in `detector` / `detector_version` /
`detector_build` / `user_git_credential` from the postgresql-specific
`postgresql.UUID(as_uuid=True)` to the SA-2.0 generic `sa.Uuid()` to
match `user.id` and the rest of the schema. On PostgreSQL both compile
to native `uuid` — `op.alter_column` emits an effective no-op DDL
(USING-cast to same type). On SQLite the column-type-string changes
from "UUID" to "CHAR(32)", so we use `op.batch_alter_table` which
SQLite supports via the copy-and-rename pattern.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3a4b5c6d7e8f"
down_revision: str | Sequence[str] | None = "90125ce5ad35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS: tuple[tuple[str, str], ...] = (
    ("detector", "id"),
    ("detector", "owner_id"),
    ("detector_version", "id"),
    ("detector_version", "detector_id"),
    ("detector_build", "id"),
    ("detector_build", "detector_id"),
    ("detector_build", "triggered_by_id"),
    ("user_git_credential", "user_id"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                type_=sa.Uuid(),
                existing_type=postgresql.UUID(as_uuid=True),
            )


def downgrade() -> None:
    for table, column in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                type_=postgresql.UUID(as_uuid=True),
                existing_type=sa.Uuid(),
            )
