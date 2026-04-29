"""Alembic migrations must produce the same schema as Base.metadata.

Without this, a `alembic revision --autogenerate` that silently drops a
column (or gets a type mismatch, or forgets an index) would pass every
other test and only surface as a 500 in prod. This test runs the migration
head against a fresh SQLite DB and compares to the ORM's in-memory schema.

Column-level comparison only checks names — not types — because SQLAlchemy
autogen + SQLite dialect variants produce spurious type diffs. Name-level
parity is the high-value contract: "every ORM column has a DB column, and
vice versa". Types are enforced implicitly by integration tests that insert
real data through the ORM.
"""

import pathlib

import pytest
from alembic import command
from alembic.config import Config
from app.config import settings
from app.models import Base
from sqlalchemy import create_engine, inspect

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def parity_db(tmp_path, monkeypatch):
    """Run `alembic upgrade head` against a fresh SQLite file, return URL."""
    db_path = tmp_path / "parity.sqlite"
    url = f"sqlite:///{db_path}"
    # env.py reads settings.DATABASE_URL — override in-process, no subprocess.
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    # Alembic resolves script_location relative to the ini file by default.
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    return url


def test_alembic_head_table_set_matches_base_metadata(parity_db):
    engine = create_engine(parity_db)
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names()) - {"alembic_version"}
    orm_tables = set(Base.metadata.tables.keys())
    missing_in_db = orm_tables - db_tables
    extra_in_db = db_tables - orm_tables
    assert not missing_in_db, (
        f"Base.metadata tables not created by alembic: {missing_in_db}"
    )
    assert not extra_in_db, (
        f"alembic created tables not in Base.metadata: {extra_in_db}"
    )


def test_alembic_head_column_names_match_base_metadata(parity_db):
    engine = create_engine(parity_db)
    inspector = inspect(engine)
    mismatches: dict[str, set[str]] = {}
    for tbl_name, tbl in Base.metadata.tables.items():
        db_cols = {c["name"] for c in inspector.get_columns(tbl_name)}
        orm_cols = {c.name for c in tbl.columns}
        if db_cols != orm_cols:
            mismatches[tbl_name] = db_cols ^ orm_cols
    assert not mismatches, f"column-name drift between migration and ORM: {mismatches}"
