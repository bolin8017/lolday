"""Verify the ix_audit_log_ts migration round-trips cleanly on aiosqlite.

The retention sweep (app/reconciler/audit_retention.py) range-deletes
``WHERE ts < cutoff``; this index keeps that an index scan rather than a
seqscan as the table grows. The index must appear after ``upgrade head`` and
be gone after ``downgrade -1``.
"""

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from app.config import settings

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent.parent

_INDEX = "ix_audit_log_ts"


def _audit_log_indexes(url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(url)
    try:
        return {ix["name"] for ix in inspect(engine).get_indexes("audit_log")}
    finally:
        engine.dispose()


@pytest.mark.no_mock_mlflow
def test_audit_log_ts_index_upgrade_downgrade_round_trip(tmp_path, monkeypatch):
    """upgrade head → index present; downgrade -1 → index absent; upgrade head again."""
    db_file = tmp_path / "audit_ts_index_round_trip.sqlite"
    url = f"sqlite:///{db_file}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))

    command.upgrade(cfg, "head")
    assert _INDEX in _audit_log_indexes(url)

    command.downgrade(cfg, "-1")
    assert _INDEX not in _audit_log_indexes(url)

    command.upgrade(cfg, "head")
    assert _INDEX in _audit_log_indexes(url)
