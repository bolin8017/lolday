"""phase12_3_role_enum_lowercase

Align ``role_enum`` to lowercase VALUEs by renaming the existing
uppercase NAMEs (``ADMIN``, ``DEVELOPER``, ``USER``) to their lowercase
equivalents. ``service_token`` is already lowercase from phase 12.2.
After this migration the enum holds ``{admin, developer, user,
service_token}`` — consistent with the codebase-wide
``values_callable`` convention used by every other enum
(``DatasetVisibility``, ``ModelVersionStage``, ``GitProvider``,
``DetectorVersionStatus``, ``DetectorBuildStatus``, ``JobType``,
``JobStatus``, ``ResourceProfile``).

This pairs with switching ``backend/app/models/user.py``'s
``SAEnum(Role, name="role_enum")`` to use
``values_callable=lambda x: [e.value for e in x]``. Together they
resolve the phase 12 case-inconsistency that caused HTTP 500 on every
Cloudflare Access service-token request to ``GET /api/v1/users/me``
(the SQLAlchemy NAME-based deserialiser raised ``LookupError`` because
``'service_token'`` wasn't a ``Role`` NAME).

PostgreSQL — ``ALTER TYPE ... RENAME VALUE`` updates the enum in-place;
existing rows automatically reflect the renamed value, no data
``UPDATE`` is required (PG ≥ 10; we run on PG 16+). The ``COMMIT``
before each RENAME mirrors phase 12.2: PostgreSQL forbids referencing a
just-renamed enum value inside the same transaction.

SQLite — ``role`` is rendered as a plain ``VARCHAR(9)`` with no CHECK
constraint (see ``test_role_enum_roundtrip``). On a fresh test DB,
alembic head + the new ``values_callable`` model write rows in the
target lowercase shape; no SQLite-specific DDL is required. The
``test_migrations_parity`` pair reads allowed values off the Python
``Role`` enum rather than the live DB so it stays green.

Note — backend rolling restart is required after this migration so
SQLAlchemy refreshes its enum metadata; stale cached connections hold
the pre-rename type definition.

Revision ID: a4b8e7c91d52
Revises: f91615e44fad
Create Date: 2026-04-28 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = "a4b8e7c91d52"
down_revision: Union[str, Sequence[str], None] = "f91615e44fad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(text("COMMIT"))
    op.execute(text("ALTER TYPE role_enum RENAME VALUE 'ADMIN' TO 'admin'"))
    op.execute(
        text("ALTER TYPE role_enum RENAME VALUE 'DEVELOPER' TO 'developer'")
    )
    op.execute(text("ALTER TYPE role_enum RENAME VALUE 'USER' TO 'user'"))


def downgrade() -> None:
    # Renaming the PG enum values back to uppercase WITHOUT also reverting
    # ``backend/app/models/user.py`` 's ``values_callable`` re-introduces
    # the LookupError this revision was created to fix. The migration
    # cannot revert the model file, so the safe contract is "downgrade
    # requires a coordinated code rollback". Refuse loudly rather than
    # silently complete and ship the operator a 500 storm at 2am.
    raise RuntimeError(
        "phase12_3_role_enum_lowercase downgrade re-introduces the "
        "case-mismatch bug fixed by this revision. The companion change "
        "in backend/app/models/user.py (the values_callable on User.role) "
        "MUST be reverted in the same deploy before running this. If you "
        "have done that, comment out this guard and rerun. See "
        "docs/phase-history/phase12.1-role-enum-bug.md for context."
    )
