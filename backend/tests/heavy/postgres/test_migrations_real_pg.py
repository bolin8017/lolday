"""Run Alembic upgrade head → downgrade base → upgrade head against a
real Postgres container. Verifies downgrade scripts still work — they
have effectively never been exercised before this test.

Per spec coverage map (§7.3 migrations): Alembic downgrade is a known
test gap; this fills it.

Why ``monkeypatch`` on ``settings.DATABASE_URL``
-------------------------------------------------
migrations/env.py reads ``settings.DATABASE_URL`` from the already-
imported ``app.config.settings`` singleton — it does NOT read from the
alembic.ini ``sqlalchemy.url`` key. Setting cfg.set_main_option() alone
has no effect. The fixture must patch the singleton directly so env.py
picks up the testcontainers URL when command.upgrade/downgrade calls
script.run_env() → loads env.py.

Known intentional no-op downgrades (Postgres cannot remove enum values)
------------------------------------------------------------------------
- 8a1c2d4e5f60  phase8_gpu2_profile          — ADD VALUE 'gpu2', no-op down
- f1e8115c3234  gpu1_resource_profile         — ADD VALUE 'gpu1', no-op down
- f91615e44fad  phase13a_detector_version_deleted_enum — ADD VALUE 'deleted', no-op down
- b2e7c8a1f330  phase10_sso_admin_email       — email rename, lossy, no-op down

These succeed silently (downgrade() is a no-op, not an error).

Known intentional raising downgrade (requires coordinated code rollback)
------------------------------------------------------------------------
- a4b8e7c91d52  phase12_3_role_enum_lowercase — raises RuntimeError by design;
  downgrade re-introduces the LookupError bug fixed by this revision.
  Reverting requires first reverting the values_callable change in
  app/models/user.py. Migration refuses loudly to prevent silent breakage.
  See docs/phase-history/phase12.1-role-enum-bug.md.

  XFAIL scope: test_upgrade_to_head_then_downgrade_to_base and
  test_each_revision_round_trips both reach this revision on the downgrade
  path. Both are marked xfail until a coordinated downgrade procedure is
  implemented (see tech-debt note below).

Tech debt — a4b8e7c91d52 downgrade
-----------------------------------
A proper downgrade of phase12_3_role_enum_lowercase would:
  1. Emit ALTER TYPE role_enum RENAME VALUE 'admin' TO 'ADMIN' (etc.)
  2. Accept that the caller has already reverted values_callable in user.py
The current guard turns a silent broken state into a loud refusal. A future
PR can implement a real downgrade by removing the guard and adding the
RENAME VALUE reversal. Track as tech debt.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

pytestmark = pytest.mark.heavy


REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"

# Revision a4b8e7c91d52 (phase12_3_role_enum_lowercase) intentionally raises
# RuntimeError in downgrade() to prevent the caller from re-introducing the
# role-enum case-mismatch LookupError without coordinating a code rollback.
# Until a real downgrade is implemented, tests that reach this revision's
# downgrade are xfailed with strict=True so they will be noticed if the
# guard is later removed.
_A4B8_XFAIL = pytest.mark.xfail(
    raises=RuntimeError,
    strict=True,
    reason=(
        "a4b8e7c91d52 (phase12_3_role_enum_lowercase) downgrade raises RuntimeError "
        "by design: reverting the enum RENAME VALUE requires a coordinated revert of "
        "the values_callable change in app/models/user.py. No automated downgrade is "
        "possible without that code change. Tech debt: implement a real downgrade "
        "and remove this xfail. See docs/phase-history/phase12.1-role-enum-bug.md."
    ),
)


@pytest.fixture
def alembic_cfg(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Config:
    """An alembic Config pointed at the testcontainers Postgres URL.

    migrations/env.py reads ``settings.DATABASE_URL`` from the already-
    imported singleton, ignoring alembic.ini's sqlalchemy.url key.
    monkeypatch overrides the singleton attribute so env.py picks up the
    testcontainers URL when command.upgrade/downgrade triggers script.run_env().

    The asyncpg URL is NOT converted here — env.py's _sync_database_url()
    does the postgresql+asyncpg → postgresql+psycopg2 rewrite itself.
    """
    import app.config  # local import to avoid early side-effects

    monkeypatch.setattr(app.config.settings, "DATABASE_URL", postgres_url)
    cfg = Config(str(ALEMBIC_INI))
    return cfg


@pytest.fixture(autouse=True)
def _reset_after_each_test(alembic_cfg: Config) -> object:
    """Best-effort teardown: unwind schema after every test so tests are
    independent without needing a fresh container each time.

    If a test failed mid-migration the schema may be in an inconsistent
    state; errors here are suppressed — the next test will surface any
    real problem.
    """
    yield
    with contextlib.suppress(Exception):
        command.downgrade(alembic_cfg, "base")


@_A4B8_XFAIL
def test_upgrade_to_head_then_downgrade_to_base(alembic_cfg: Config) -> None:
    """Apply every revision forward, then unwind them all backward.

    XFAIL: hits a4b8e7c91d52 downgrade which raises RuntimeError by design.
    Remove the xfail marker once a real downgrade is implemented.

    All other downgrade() functions succeed (no-ops for enum ADD VALUE
    revisions, real DROP/ALTER for schema revisions).
    """
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")


def test_upgrade_head_is_idempotent(alembic_cfg: Config) -> None:
    """A second upgrade head call after the first must be a no-op.

    Alembic records which revisions have been applied and skips already-
    applied ones; if any upgrade function is not idempotent (e.g. creates
    a table unconditionally without IF NOT EXISTS) it will raise on the
    second call.
    """
    command.upgrade(alembic_cfg, "head")
    command.upgrade(alembic_cfg, "head")  # must not raise


@_A4B8_XFAIL
def test_each_revision_round_trips(alembic_cfg: Config) -> None:
    """For every revision in the chain: apply all in order, then unwind
    one at a time.

    XFAIL: hits a4b8e7c91d52 downgrade which raises RuntimeError by design.
    Remove the xfail marker once a real downgrade is implemented.

    walk_revisions yields head → base; reverse for chronological upgrade
    order. After walking forward through every revision, step backward
    through each one individually.

    This differs from test_upgrade_to_head_then_downgrade_to_base in that
    it exercises the downgrade path of each individual revision in
    isolation (not as a batch), catching issues where one revision's
    downgrade leaves state that breaks a sibling revision's downgrade.
    """
    script = ScriptDirectory.from_config(alembic_cfg)
    revisions = [rev.revision for rev in script.walk_revisions()]
    # walk_revisions yields head → base; reverse for chronological order
    revisions.reverse()

    for rev in revisions:
        command.upgrade(alembic_cfg, rev)

    for _ in revisions:
        command.downgrade(alembic_cfg, "-1")


@pytest.mark.xfail(
    reason=(
        "Postgres cannot remove enum values added by ADD VALUE. "
        "Revisions 8a1c2d4e5f60, f1e8115c3234, f91615e44fad intentionally "
        "no-op their downgrade() — the enum types (resource_profile_enum, "
        "detector_version_status) retain the added values after downgrade base. "
        "Per-spec, these migrations are forward-only. This xfail documents "
        "the known gap; fix would require pg_catalog-level type recreation "
        "in each downgrade(), which is risky on a live database. "
        "Additionally, a4b8e7c91d52 raises before reaching this assertion."
    ),
    strict=False,  # currently fails; strict=False so xpass would be surfaced
)
def test_downgrade_fully_reverts_schema(alembic_cfg: Config) -> None:
    """After downgrade to base, no application tables should remain.

    This test is XFAIL for two reasons:
    1. a4b8e7c91d52 downgrade raises RuntimeError, so we never reach base.
    2. Even if (1) were fixed, four enum-ADD-VALUE revisions have no-op
       downgrades that leave residual enum type values in pg_type.

    If both issues are fixed, remove the xfail and verify the assertion.
    """
    import sqlalchemy as sa  # local import to keep top-level clean

    sync_url = _postgres_sync_url(alembic_cfg)
    engine = sa.create_engine(sync_url)

    try:
        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "base")

        with engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' "
                    "  AND tablename != 'alembic_version'"
                )
            )
            remaining = [row[0] for row in result]
    finally:
        engine.dispose()

    assert remaining == [], f"Tables remain after downgrade base: {remaining}"


def _postgres_sync_url(cfg: Config) -> str:  # cfg reserved for future use
    """Extract the psycopg2 URL that env.py would use, from the patched settings."""
    import app.config

    url = app.config.settings.DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url
