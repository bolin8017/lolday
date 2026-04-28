"""phase12_1_service_token_friendly_name

Rename CF Access service-token User rows whose ``display_name`` is still
the auto-derived raw email local-part (shaped ``service-<64-hex>.access``)
to the friendly fixed label. Without this, an existing service-token row
keeps surfacing as ``@service-...access`` in any admin UI that renders
`display_name`, and in Discord events emitted before the
``_user_context`` skip lands.

Idempotent: only rewrites rows that still match the auto-derived form,
so re-running on a DB an admin already customised is a no-op.

Revision ID: c7e3a9b1d042
Revises: 12f13a2e3d68
Create Date: 2026-04-28 10:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "c7e3a9b1d042"
down_revision: Union[str, Sequence[str], None] = "12f13a2e3d68"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FRIENDLY = "Internal service token"


def _candidate_rows(conn) -> list[tuple[str, str, str]]:
    """Fetch (id, email, display_name) for service-token rows. Raw SQL +
    Python filtering keeps us DB-agnostic — `split_part` is PG-only and
    Alembic's online tests run against SQLite.
    """
    return [
        (row[0], row[1], row[2])
        for row in conn.execute(
            text(
                'SELECT id, email, display_name FROM "user" '
                "WHERE email LIKE 'service-%@cf-access.local'"
            )
        )
    ]


def upgrade() -> None:
    conn = op.get_bind()
    for user_id, email, display_name in _candidate_rows(conn):
        raw_local = email.split("@", 1)[0]
        if display_name == raw_local:
            conn.execute(
                text(
                    'UPDATE "user" SET display_name = :friendly WHERE id = :id'
                ),
                {"friendly": _FRIENDLY, "id": user_id},
            )


def downgrade() -> None:
    # Restore the auto-derived raw local-part for any row that still
    # carries the friendly label. Same idempotency guard as upgrade —
    # never clobber a manually-customised name.
    conn = op.get_bind()
    for user_id, email, display_name in _candidate_rows(conn):
        if display_name == _FRIENDLY:
            raw_local = email.split("@", 1)[0]
            conn.execute(
                text(
                    'UPDATE "user" SET display_name = :raw WHERE id = :id'
                ),
                {"raw": raw_local, "id": user_id},
            )
