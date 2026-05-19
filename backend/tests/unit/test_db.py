"""Direct-call tests for ``app.db``.

`app/db.py` declares the production async engine + session maker, plus
two side bits of behaviour that every other test bypasses:

- The ``_enable_sqlite_busy_timeout`` connect-listener that sets
  ``PRAGMA busy_timeout = 30000`` when the engine speaks SQLite
  (Playwright live-stack via aiosqlite). Production Postgres ignores
  the PRAGMA because the listener early-returns on non-sqlite URLs.
- The ``get_async_session`` FastAPI dependency factory that yields an
  ``AsyncSession`` from the production maker. Every integration test
  overrides this dependency to point at the per-test aiosqlite engine,
  so the production code path was unreachable.

Without these tests, a refactor that flipped the early-return polarity
(making the PRAGMA fire on Postgres) or that dropped the connect
listener entirely would silently disarm the Playwright lock-up
defence — and the live-stack would re-emerge as a flaky CI failure.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_dbapi_with_cursor():
    """Return ``(dbapi_connection, cursor)`` where the cursor has the
    expected ``execute`` / ``close`` MagicMock surface."""
    cursor = MagicMock()
    dbapi = MagicMock()
    dbapi.cursor.return_value = cursor
    return dbapi, cursor


def test_sqlite_busy_timeout_pragma_fires_on_sqlite_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``DATABASE_URL`` starts with ``sqlite``, the listener must
    set ``PRAGMA busy_timeout = 30000`` so multi-context Playwright
    specs (e.g. ``tests/e2e/models/transfer-and-delete.spec.ts``) don't
    bomb with ``OperationalError: database is locked``."""
    from app import db

    monkeypatch.setattr(db.settings, "DATABASE_URL", "sqlite+aiosqlite:///test.db")
    dbapi, cursor = _make_dbapi_with_cursor()
    db._enable_sqlite_busy_timeout(dbapi, SimpleNamespace())

    cursor.execute.assert_called_once_with("PRAGMA busy_timeout = 30000")
    cursor.close.assert_called_once()


def test_sqlite_busy_timeout_pragma_skipped_on_postgres_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a Postgres / asyncpg URL the listener MUST early-return.
    Without this guard, asyncpg connections would receive an unsupported
    PRAGMA and bubble a 500 at first request (Postgres doesn't honor
    SQLite PRAGMAs)."""
    from app import db

    monkeypatch.setattr(
        db.settings, "DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db"
    )
    dbapi, cursor = _make_dbapi_with_cursor()
    db._enable_sqlite_busy_timeout(dbapi, SimpleNamespace())

    # No cursor was opened; the PRAGMA was never sent.
    dbapi.cursor.assert_not_called()
    cursor.execute.assert_not_called()


async def test_get_async_session_yields_a_session_then_closes() -> None:
    """``Depends(get_async_session)`` is FastAPI's entry into the
    production engine. The generator yields exactly one AsyncSession and
    cleans up via the ``async with`` context manager. Exercise it
    directly so the production code path appears in coverage (the
    integration tests override the dependency to a per-test maker)."""
    from app.db import get_async_session
    from sqlalchemy.ext.asyncio import AsyncSession

    gen = get_async_session()
    session = await gen.__anext__()
    assert isinstance(session, AsyncSession)
    # The generator should terminate cleanly after exiting the context.
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()
