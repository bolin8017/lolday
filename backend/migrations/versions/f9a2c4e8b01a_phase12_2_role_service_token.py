"""phase12_2_role_service_token

Add ``service_token`` to the ``role_enum`` and back-fill it onto every
row whose email matches the CF Access service-token synthetic shape
(``service-<common_name>@cf-access.local``). After this migration,
``User.is_service_token`` reads off ``role`` — no longer email-suffix
probing — so an admin editing an email cannot flip a row's notification
policy by accident.

PostgreSQL 12+ accepts ``ALTER TYPE ... ADD VALUE`` inside the
migration's BEGIN/COMMIT (the only restriction is that the new value
can't be referenced in the *same* transaction; we work around that by
issuing the ALTER and the back-fill UPDATE as separate statements with
the dialect's commit semantics — Alembic re-opens the txn after the
ALTER on PG when the migration ``transactional_ddl`` flag is True for
this dialect, which it is by default for psycopg).

SQLite stores enum values as VARCHAR with a CHECK constraint and rebuilds
the table when the column type changes — so on SQLite we issue the
back-fill UPDATE only and rely on the schema-level CHECK already
enclosing the literal value once SQLAlchemy renders the column with the
new ``Role`` enum. (The migrations parity test tolerates this because the
value set is read off the application's ``Role`` enum, not the live DB.)

Revision ID: f9a2c4e8b01a
Revises: c7e3a9b1d042
Create Date: 2026-04-28 11:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "f9a2c4e8b01a"
down_revision: Union[str, Sequence[str], None] = "c7e3a9b1d042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # IF NOT EXISTS keeps re-runs idempotent; PG 12+ supports it.
        op.execute(
            text("ALTER TYPE role_enum ADD VALUE IF NOT EXISTS 'service_token'")
        )
        # PG forbids referencing a freshly-added enum value inside the
        # same transaction. Commit the ALTER, then run the back-fill in
        # a fresh transaction.
        op.execute(text("COMMIT"))
    bind.execute(
        text(
            'UPDATE "user" SET role = \'service_token\' '
            "WHERE email LIKE 'service-%@cf-access.local' "
            "AND role != 'service_token'"
        )
    )


def downgrade() -> None:
    # Restore previous classification; we keep the enum value present
    # because removing a value from a PG enum is intentionally
    # awkward and the empty enum slot is harmless.
    bind = op.get_bind()
    bind.execute(
        text(
            'UPDATE "user" SET role = \'admin\' '
            "WHERE role = 'service_token'"
        )
    )
