"""E2E live-stack schema bootstrap (frontend-slow workflow).

The playwright `webServer` config spawns uvicorn against a fresh
`sqlite+aiosqlite:///file::memory:?cache=shared&uri=true` URL. Neither the
production `alembic-upgrade` helm hook nor the test conftest's
`Base.metadata.create_all` runs in that path, so the reconciler's first
tick crashed with `no such table: detector_build`. The lifespan now
self-bootstraps the schema when AUTH_DEV_MODE=true and the DB is empty;
production rejects AUTH_DEV_MODE=true at boot via Settings model_validator,
so this branch can never fire in prod.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def empty_engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_bootstrap_creates_tables_when_dev_mode_and_empty(
    monkeypatch, empty_engine
):
    from app import main as app_main
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    monkeypatch.setattr(app_main, "engine", empty_engine)

    await app_main._bootstrap_dev_schema_if_empty()

    async with empty_engine.begin() as conn:

        def _names(sync_conn):
            return sa.inspect(sync_conn).get_table_names()

        names = await conn.run_sync(_names)
    assert "detector_build" in names
    assert "job" in names


@pytest.mark.asyncio
async def test_bootstrap_noop_when_dev_mode_off(monkeypatch, empty_engine):
    from app import main as app_main
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(app_main, "engine", empty_engine)

    await app_main._bootstrap_dev_schema_if_empty()

    async with empty_engine.begin() as conn:

        def _names(sync_conn):
            return sa.inspect(sync_conn).get_table_names()

        names = await conn.run_sync(_names)
    assert names == []


@pytest.mark.asyncio
async def test_bootstrap_noop_when_tables_already_exist(monkeypatch, empty_engine):
    from app import main as app_main
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    monkeypatch.setattr(app_main, "engine", empty_engine)

    async with empty_engine.begin() as conn:
        await conn.execute(sa.text("CREATE TABLE foo (id INTEGER PRIMARY KEY)"))

    await app_main._bootstrap_dev_schema_if_empty()

    async with empty_engine.begin() as conn:

        def _names(sync_conn):
            return sa.inspect(sync_conn).get_table_names()

        names = await conn.run_sync(_names)
    assert names == ["foo"]
