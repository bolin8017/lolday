"""Contract-tier fixtures: schemathesis app loader + respx replay-tape
loader + shared auth/engine helper. Contract tests run serial (one FastAPI
instance per process — schemathesis hits a single ASGI app and parallel
workers would conflict).

Tests under backend/tests/contract/ carry @pytest.mark.contract.

OAS 3.1 note: FastAPI emits OpenAPI 3.1.0. schemathesis 3.x does not
fully support 3.1 natively, but provides an experimental down-converter
(schemathesis.experimental.OPEN_API_3_1) that remaps 3.1-only keywords
to 3.0 equivalents before loading. Enabled here once at module import.

Shared auth helper
------------------
``install_contract_auth`` is an async context manager (not a fixture) used
by the per-file autouse fixtures in each openapi test module.  It:

1. Replaces ``app.db.engine`` and ``app.main.engine`` with the test SQLite
   engine so the ASGI lifespan's ``_assert_schema_at_head()`` reads from the
   test DB (no alembic_version → returns early without a Postgres DNS lookup).
2. Seeds a test user row with the requested email and role.
3. Installs a header-based ``_fake_auth`` dependency override that resolves
   any ``X-Test-User-Email`` header to the seeded user row.
4. Tears down the overrides and restores the original engines on exit.

Each test file that uses schemathesis defines its own ``autouse`` fixture
that calls ``install_contract_auth`` with its own email/role, then injects
``X-Test-User-Email`` into every generated request.  This pattern keeps
auth setup explicit per file while eliminating the ~80-line duplication.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient

# Enable experimental OAS 3.1 → 3.0 down-conversion before loading any
# schema.  Must be called before the first schemathesis.from_asgi() call.
schemathesis.experimental.OPEN_API_3_1.enable()

from app.main import app  # noqa: E402  # import after schemathesis env is set up

REPLAY_TAPE_DIR = Path(__file__).parent.parent / "fixtures" / "mlflow" / "recorded"


@pytest.fixture(scope="session")
def fastapi_app():
    """Return the FastAPI app instance. Tests can override deps as needed."""
    return app


@pytest.fixture(scope="session")
def schema(fastapi_app):
    """schemathesis schema loaded from the running app's /openapi.json."""
    return schemathesis.from_asgi("/openapi.json", fastapi_app)


@pytest.fixture
def client(fastapi_app) -> TestClient:
    return TestClient(fastapi_app)


@pytest.fixture
def mlflow_replay_tape(request):
    """Load a recorded MLflow response tape by file name (used by T17).

    Usage: parametrize with the tape filename, e.g.
        @pytest.mark.parametrize("mlflow_replay_tape", ["create_run.json"], indirect=True)
    """
    tape_name = getattr(request, "param", None)
    if not tape_name:
        return None
    with (REPLAY_TAPE_DIR / tape_name).open() as f:
        return json.load(f)


@contextlib.asynccontextmanager
async def install_contract_auth(
    email: str,
    role,  # app.models.Role value
) -> AsyncIterator[None]:
    """Async context manager: install contract auth overrides for one test.

    Patches both engine references so the ASGI lifespan runs cleanly against
    the SQLite test DB, seeds a user row with *email* and *role*, and installs
    a header-based fake-auth dependency override.  Tears everything down on
    exit.

    Intended use (in a per-file autouse fixture):

        @pytest_asyncio.fixture(autouse=True)
        async def _install_auth(setup_db):
            async with install_contract_auth("me@example.dev", Role.USER):
                yield
    """
    import app.db as _app_db
    import app.main as _app_main
    from app.auth.cf_access import cf_access_user as _cf_dep
    from app.db import get_async_session
    from app.models import User
    from app.services.user_handle import derive_handle_from_email, next_unique_handle
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.conftest import test_engine, test_session_maker

    # Patch engines so the lifespan's _assert_schema_at_head() uses SQLite.
    # app.main does `from app.db import engine` (local binding), so we must
    # patch both module-level names.
    original_db_engine = _app_db.engine
    original_main_engine = _app_main.engine
    _app_db.engine = test_engine
    _app_main.engine = test_engine

    async def _override_session():
        async with test_session_maker() as session:
            yield session

    # Seed the test user in the freshly created test DB.
    async with test_session_maker() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is None:
            existing_handles = set(
                (await session.execute(select(User.handle))).scalars().all()
            )
            base_handle = derive_handle_from_email(email)
            handle = next_unique_handle(base_handle, existing=existing_handles)
            user = User(
                email=email,
                handle=handle,
                role=role,
                display_name=email.split("@")[0],
            )
            session.add(user)
            await session.commit()

    # Header-based fake auth: looks up user by X-Test-User-Email header.
    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        addr = request.headers.get("x-test-user-email")
        if not addr:
            raise HTTPException(401, "missing X-Test-User-Email (contract fixture)")
        row = (
            await session.execute(select(User).where(User.email == addr))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"contract fixture: user not seeded: {addr}")
        return row

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[_cf_dep] = _fake_auth

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(_cf_dep, None)
        _app_db.engine = original_db_engine
        _app_main.engine = original_main_engine
