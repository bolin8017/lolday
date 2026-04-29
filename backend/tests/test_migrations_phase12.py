"""Content tests for the two service-token migrations.

`test_migrations_parity` already covers schema-level shape; these tests
add row-level coverage so a regression in the actual UPDATE / ALTER
statements gets caught here instead of at deploy time.

Both migrations claim idempotency — re-running them must be a no-op
on already-migrated rows and never clobber an admin-customised
display_name.
"""

import pathlib
import uuid

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.config import settings

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
_FRIENDLY = "Internal service token"
_RAW = "service-abc123def456789a.access"
_EMAIL = f"{_RAW}@cf-access.local"


def _alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "phase12_content.sqlite"
    url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    return cfg, url


def _stamp_then_seed_then_run(cfg, url, target, rows):
    """Run alembic up to ``target`` migration, seed rows on the
    pre-migration revision, then continue alembic to head.
    """
    parent_rev = {
        "c7e3a9b1d042": "12f13a2e3d68",
        "f9a2c4e8b01a": "c7e3a9b1d042",
    }[target]
    command.upgrade(cfg, parent_rev)
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                sa.text(
                    'INSERT INTO "user" '
                    "(id, email, hashed_password, role, display_name, "
                    "is_active, is_verified, is_superuser) "
                    "VALUES (:id, :email, :pw, :role, :dn, 1, 1, 0)"
                ),
                row,
            )
    command.upgrade(cfg, target)
    return engine


def _read_user(engine, email):
    with engine.connect() as conn:
        return conn.execute(
            sa.text('SELECT display_name, role FROM "user" WHERE email = :e'),
            {"e": email},
        ).one()


def test_phase12_1_renames_only_raw_displayname(tmp_path, monkeypatch):
    """Migration ``c7e3a9b1d042`` rewrites the auto-derived raw
    display_name; admin-customised names + plain users are untouched."""
    cfg, url = _alembic_cfg(tmp_path, monkeypatch)
    engine = _stamp_then_seed_then_run(
        cfg,
        url,
        "c7e3a9b1d042",
        rows=[
            # service-token row with raw auto-derived display_name → renamed
            {
                "id": uuid.uuid4().hex,
                "email": _EMAIL,
                "pw": "!",
                "role": "user",
                "dn": _RAW,
            },
            # service-token row with custom display_name → preserved
            {
                "id": uuid.uuid4().hex,
                "email": "service-other.access@cf-access.local",
                "pw": "!",
                "role": "user",
                "dn": "Custom Bot",
            },
            # plain user → never touched
            {
                "id": uuid.uuid4().hex,
                "email": "alice@example.com",
                "pw": "!",
                "role": "user",
                "dn": "alice",
            },
        ],
    )

    assert _read_user(engine, _EMAIL).display_name == _FRIENDLY
    assert (
        _read_user(engine, "service-other.access@cf-access.local").display_name
        == "Custom Bot"
    )
    assert _read_user(engine, "alice@example.com").display_name == "alice"


def test_phase12_1_is_idempotent(tmp_path, monkeypatch):
    """Re-running the same migration is a no-op on the already-renamed
    row (the alembic version table prevents a real re-run; we simulate
    by invoking the upgrade callable directly twice)."""
    from migrations.versions import (
        c7e3a9b1d042_phase12_1_service_token_friendly_name as mod,
    )

    cfg, url = _alembic_cfg(tmp_path, monkeypatch)
    command.upgrade(cfg, "12f13a2e3d68")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                'INSERT INTO "user" '
                "(id, email, hashed_password, role, display_name, "
                "is_active, is_verified, is_superuser) "
                "VALUES (:id, :email, '!', 'user', :dn, 1, 1, 0)"
            ),
            {"id": uuid.uuid4().hex, "email": _EMAIL, "dn": _RAW},
        )

    with engine.begin() as conn:
        # Direct invocation simulating two consecutive ``op.upgrade()``
        # calls. The second pass must NOT find any candidate row.
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        ctx = MigrationContext.configure(connection=conn)
        op_proxy = Operations(ctx)
        import alembic.op

        alembic.op._proxy = op_proxy
        try:
            mod.upgrade()
            mod.upgrade()
        finally:
            alembic.op._proxy = None

    assert _read_user(engine, _EMAIL).display_name == _FRIENDLY


def test_phase12_2_backfills_role_only_for_service_tokens(tmp_path, monkeypatch):
    """Migration ``f9a2c4e8b01a`` adds the ``service_token`` enum value
    AND back-fills role for any service-token-shaped email."""
    cfg, url = _alembic_cfg(tmp_path, monkeypatch)
    engine = _stamp_then_seed_then_run(
        cfg,
        url,
        "f9a2c4e8b01a",
        rows=[
            {
                "id": uuid.uuid4().hex,
                "email": _EMAIL,
                "pw": "!",
                "role": "admin",
                "dn": _FRIENDLY,
            },
            {
                "id": uuid.uuid4().hex,
                "email": "alice@example.com",
                "pw": "!",
                "role": "user",
                "dn": "alice",
            },
        ],
    )
    assert _read_user(engine, _EMAIL).role == "service_token"
    assert _read_user(engine, "alice@example.com").role == "user"
