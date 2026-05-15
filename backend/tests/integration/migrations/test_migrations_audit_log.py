"""Verify the new audit_log migration upgrades and downgrades cleanly on aiosqlite."""

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from app.config import settings

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent


@pytest.mark.no_mock_mlflow
def test_audit_log_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    """Upgrade head, downgrade one step, upgrade head — schema must reach head both times.

    The migration test mirrors ``test_migrations_parity.py``'s pattern of
    monkeypatching ``settings.DATABASE_URL`` because ``env.py`` reads the URL
    from settings, not from the Alembic ``Config`` argument.
    """
    db_file = tmp_path / "audit_round_trip.sqlite"
    url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    # The model is importable + table present after the round trip.
    from sqlalchemy import create_engine, inspect

    engine = create_engine(url)
    assert "audit_log" in inspect(engine).get_table_names()
