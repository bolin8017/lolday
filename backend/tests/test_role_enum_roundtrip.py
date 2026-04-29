"""Phase 12.1 regression — role_enum case-inconsistency.

Phase 7.5 baseline created ``role_enum`` with uppercase NAMES
(``'ADMIN', 'DEVELOPER', 'USER'``) — i.e. SQLAlchemy's NAME-based default
for ``SAEnum``. Phase 12.2 added ``'service_token'`` as a lowercase VALUE,
producing the mixed-case enum ``{ADMIN, DEVELOPER, USER, service_token}``
on PostgreSQL and a VARCHAR storing equivalent strings on SQLite. Reading
a service-token user back through the ORM raised ``LookupError`` —
``'service_token'`` is not among the Role enum's NAMES — surfacing as
HTTP 500 on every CF Access service-token request.

Phase 12.1 root-cause fix: align ``role_enum`` to lowercase VALUES so the
model + migrations agree, matching the codebase-wide ``values_callable``
convention used by every other enum (``DatasetVisibility``,
``ModelVersionStage``, ``GitProvider``, ``DetectorVersionStatus``,
``DetectorBuildStatus``, ``JobType``, ``JobStatus``, ``ResourceProfile``).

These tests exercise the alembic-head schema (matching prod) and lock
down two contracts:

* every Role member's ORM round-trip stores the lowercase VALUE
* a raw row inserted with VALUE ``'service_token'`` (the shape phase
  12.2's UPDATE produces in prod) reads back as ``Role.SERVICE_TOKEN``
"""
import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Role, User


_PROJECT_ROOT = pathlib.Path(__file__).parent.parent


def _alembic_head_engine(tmp_path, monkeypatch):
    """Run alembic upgrade head against a fresh SQLite DB and return a
    sync engine bound to it. Mirrors ``test_migrations_phase12``'s
    setup so we exercise the real migration sequence rather than the
    fast-path ``Base.metadata.create_all`` used elsewhere.
    """
    db_path = tmp_path / "role_enum.sqlite"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return sa.create_engine(url)


@pytest.mark.parametrize("role", list(Role))
def test_role_orm_writes_lowercase_value_and_roundtrips(
    role, tmp_path, monkeypatch
):
    """ORM insert of every Role member must store the lowercase VALUE
    in the DB and read back as the same Role member.

    Regression for the phase 12 NAME-vs-VALUE mismatch (see migration
    ``a4b8e7c91d52_phase12_3_role_enum_lowercase``) — without
    ``values_callable``, the ORM serialised Role members to uppercase
    NAMEs, contradicting phase 12.2's lowercase-VALUE backfill
    convention and every other enum in the codebase.
    """
    engine = _alembic_head_engine(tmp_path, monkeypatch)
    user_id = uuid.uuid4()

    with Session(engine) as session:
        session.add(
            User(
                id=user_id,
                email=f"role-{role.name.lower()}@example.dev",
                role=role,
                display_name=role.name,
            )
        )
        session.commit()

    with engine.connect() as conn:
        raw = conn.execute(
            sa.text('SELECT role FROM "user" WHERE id = :id'),
            {"id": user_id.hex},
        ).scalar_one()
    assert raw == role.value, (
        f"DB stored {raw!r} for Role.{role.name}; expected the lowercase "
        f"VALUE {role.value!r} (codebase-wide values_callable convention)"
    )

    with Session(engine) as session:
        rehydrated = session.get(User, user_id)
        assert rehydrated is not None
        assert rehydrated.role == role
        assert rehydrated.role.value == role.value
        assert rehydrated.role.name == role.name


def test_role_user_default_stores_lowercase(tmp_path, monkeypatch):
    """Omitting the ``role=`` kwarg picks up ``default=Role.USER`` from the
    column. Phase 12.1 regression: the default must serialise through
    the same ``values_callable`` path so the row stores ``'user'`` (not
    ``'USER'``). A future change that bypasses the column default for a
    Pythonic ``__init__`` default would silently re-introduce the
    NAME-storage shape this PR exists to remove.
    """
    engine = _alembic_head_engine(tmp_path, monkeypatch)
    user_id = uuid.uuid4()

    with Session(engine) as session:
        session.add(
            User(
                id=user_id,
                email="default-role@example.dev",
                # role= intentionally omitted — exercises the column default.
                display_name="default-role",
            )
        )
        session.commit()

    with engine.connect() as conn:
        raw = conn.execute(
            sa.text('SELECT role FROM "user" WHERE id = :id'),
            {"id": user_id.hex},
        ).scalar_one()
    assert raw == Role.USER.value, (
        f"DB stored {raw!r} for the column default; expected lowercase "
        f"VALUE {Role.USER.value!r}"
    )


def test_service_token_lowercase_value_reads_via_orm(tmp_path, monkeypatch):
    """A user row with ``role`` stored as the lowercase VALUE
    ``'service_token'`` (the exact shape phase 12.2's UPDATE produces in
    prod when back-filling existing CF Access service-token email rows)
    must read back through the ORM as ``Role.SERVICE_TOKEN``.

    Locks the deserialiser path independently of how the model writes:
    test 1's parametrize covers ORM-write→ORM-read, this test covers
    raw-SQL-write (matching the prod migration shape) → ORM-read. They
    fail in different stack frames on a NAME-only model — the prod 500
    on ``GET /api/v1/users/me`` reproduced exactly the ``LookupError``
    this assertion guards against.
    """
    engine = _alembic_head_engine(tmp_path, monkeypatch)
    user_id = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'INSERT INTO "user" '
                "(id, email, role, display_name) "
                "VALUES (:id, :email, :role, :dn)"
            ),
            {
                "id": user_id.hex,
                "email": "service-test@cf-access.local",
                "role": "service_token",
                "dn": "Internal service token",
            },
        )

    with Session(engine) as session:
        user = session.get(User, user_id)
        assert user is not None, "raw 'service_token' row failed to round-trip"
        assert user.role == Role.SERVICE_TOKEN
        assert user.role.value == "service_token"
