import os

from cryptography.fernet import Fernet

# H-17a: never reuse a hardcoded Fernet key in tests — the value would be
# public via git, and any operator who copies it into .lolday-secrets.env
# makes encrypted columns trivially decryptable. Per-session fresh key.
os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
os.environ.setdefault("RECONCILER_ENABLED", "false")
os.environ.setdefault("FIFO_RECONCILER_ENABLED", "false")
os.environ.setdefault("SAMPLES_LOCAL_ROOT", "/nonexistent-samples-root-for-tests")
# Phase 10.2: opt out of production SSO validation — tests use dependency_override
# to inject fake users, so CF_ACCESS_* can stay blank.
os.environ.setdefault("ENVIRONMENT", "test")
# #165: default DOCS_ENABLED flipped to False in app.config. Most tests
# don't touch /docs or /openapi.json, but ``test_metrics_not_in_openapi_schema``
# does. Set the test env to enable so existing assertions still hold; the
# dedicated #165 test toggles it back to False to assert the gate fires.
os.environ.setdefault("DOCS_ENABLED", "true")

import pytest
import pytest_asyncio
import sqlalchemy as sa
from app.db import get_async_session
from app.models import Base, Role, User
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# xdist runs each worker in its own process; PYTEST_XDIST_WORKER is set
# by xdist/remote.py before any conftest imports.  Give each worker its own
# DB file so workers don't collide on create_all / drop_all.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "main")
TEST_DATABASE_URL = f"sqlite+aiosqlite:///./test_{_WORKER_ID}.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

# SQLite disables foreign-key enforcement by default.  Enable it so that
# ondelete=CASCADE on ModelVersion.registered_model_id (and similar FKs)
# is actually enforced during tests, matching production Postgres behaviour.
from sqlalchemy import event  # noqa: E402  # must come after test_engine is created


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_fk_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Disable FK enforcement for teardown so that drop_all succeeds even
    # when there are unresolvable cyclic FKs (job ↔ model_version).
    async with test_engine.begin() as conn:
        await conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)


async def _make_user(
    email: str,
    role: Role = Role.USER,
) -> User:
    """Insert or return a test User row with the given role."""
    async with test_session_maker() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        from app.services.user_handle import (
            derive_handle_from_email,
            next_unique_handle,
        )
        from sqlalchemy import select as _select

        existing_handles = set(
            (await session.execute(_select(User.handle))).scalars().all()
        )
        base_handle = derive_handle_from_email(email)
        handle = next_unique_handle(base_handle, existing=existing_handles)
        user = User(
            email=email,
            handle=handle,
            role=role,
            display_name=email.split("@", 1)[0],
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _install_header_based_auth_override() -> None:
    """Install a cf_access_user override keyed by the X-Test-User-Email request header.

    This preserves per-client identity when tests run two clients side-by-side
    (e.g. `user_client` + `second_user_client`). The real production dependency
    parses identity out of a JWT; in tests each client just sets a test header
    pointing at a pre-seeded user row.
    """
    from app.auth.cf_access import cf_access_user
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.ext.asyncio import AsyncSession

    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email (test fixture)")
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"test fixture: user not seeded: {email}")
        return row

    app.dependency_overrides[cf_access_user] = _fake_auth


@pytest_asyncio.fixture
async def client():
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    _install_header_based_auth_override()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def internal_client():
    """AsyncClient bound to the internal sub-app (production port 8001).

    /api/v1/internal/* routes were split off ``app.main:app`` in
    M-internal-split — production now serves them via ``app.internal_app``
    on container port 8001, gated by NetworkPolicy to lolday-jobs only.
    Tests that hit ``/api/v1/internal/*`` must use this client; auth is
    via the ``Authorization: Bearer <job-token>`` header (require_job_token),
    no CF Access user override is needed.
    """
    from app.internal_app import internal_app

    async def override():
        async with test_session_maker() as session:
            yield session

    internal_app.dependency_overrides[get_async_session] = override
    transport = ASGITransport(app=internal_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    internal_app.dependency_overrides.clear()


def _as_user(client: AsyncClient, email: str) -> AsyncClient:
    client.headers["x-test-user-email"] = email
    return client


@pytest_asyncio.fixture
async def auth_client_user(client):
    await _make_user("user@example.dev", role=Role.USER)
    return _as_user(client, "user@example.dev")


@pytest_asyncio.fixture
async def auth_client_developer(client):
    await _make_user("dev@example.dev", role=Role.DEVELOPER)
    return _as_user(client, "dev@example.dev")


@pytest_asyncio.fixture
async def auth_client_admin(client):
    await _make_user("adm@example.dev", role=Role.ADMIN)
    return _as_user(client, "adm@example.dev")


@pytest_asyncio.fixture
async def auth_client_service_token(client):
    """CF Access service-token principal — role=SERVICE_TOKEN, synthesised
    ``service-<name>@cf-access.local`` email shape (matches what
    ``app.auth.cf_access`` mints for service-token JWTs)."""
    await _make_user("service-test@cf-access.local", role=Role.SERVICE_TOKEN)
    return _as_user(client, "service-test@cf-access.local")


@pytest_asyncio.fixture
async def user_client(client):
    """Alias used by dataset tests; regular USER-role user."""
    await _make_user("user1@example.dev", role=Role.USER)
    return _as_user(client, "user1@example.dev")


@pytest_asyncio.fixture
async def second_user_client():
    """Distinct client with a different user so it can run alongside user_client."""
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    _install_header_based_auth_override()
    await _make_user("user2@example.dev", role=Role.USER)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["x-test-user-email"] = "user2@example.dev"
        yield c


@pytest_asyncio.fixture
async def db_session():
    async with test_session_maker() as session:
        yield session


def pytest_collection_modifyitems(config, items):
    """Reject @pytest.mark.flaky_tracked without an issue URL.

    .claude/rules/testing.md quarantine workflow: every flaky-tracked test
    must carry issue=<github-url>. The hook is enforced at collection so
    tests cannot ship without traceability.
    """
    for item in items:
        for marker in item.iter_markers(name="flaky_tracked"):
            issue = marker.kwargs.get("issue")
            if not issue or not issue.startswith("https://github.com/"):
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.flaky_tracked requires "
                    f"issue=<github-url> kwarg; got {issue!r}"
                )
