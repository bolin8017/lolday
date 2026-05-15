"""Schemathesis property-based contract test for /api/v1/jobs endpoints.

Schemathesis enumerates every operation matching /api/v1/jobs* in the
OpenAPI document FastAPI generates from the route definitions and
auto-generates up to 50 cases per operation (boundary values, nullables,
unicode strings, integer overflow, etc.). Each case is sent against the
running ASGI app and the response is validated against the documented
response schema.

This test catches:
- OpenAPI <-> actual response drift (e.g. handler returns a field the
  schema doesn't declare)
- Validation regressions (handler returns 500 where it should return 422)
- Status-code documentation gaps (handler returns 503 with no schema entry)

Auth setup: the standard X-Test-User-Email / cf_access_user dependency
override from the integration tier is reused here. A per-test autouse
fixture installs the overrides and seeds the test user row.

Lifespan setup: schemathesis's call_asgi() uses starlette_testclient
(which enters the ASGI lifespan), unlike httpx.ASGITransport (which does
not). The lifespan calls _assert_schema_at_head() against app.db.engine.
In tests that engine points at the real Postgres URL. The autouse fixture
overrides app.db.engine with the SQLite test engine so _assert_schema_at_head
reads from the test DB (which has no alembic_version table and returns early
via the OperationalError catch). It also sets app.state.http and
app.state.mlflow ahead of time so the lifespan's explicit setup doesn't
create duplicate clients.

schemathesis.from_pytest_fixture("schema") creates a LazySchema that
defers loading the actual schemathesis.BaseSchema until the "schema"
fixture resolves — this is the recommended pattern for referencing a
conftest-defined fixture at module level.

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
S7.3 routers/jobs.py coverage map.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import schemathesis
from hypothesis import settings as h_settings
from sqlalchemy import select

pytestmark = pytest.mark.contract

_TEST_USER_EMAIL = "contract-jobs@example.dev"

# ---------------------------------------------------------------------------
# LazySchema bound to the "schema" conftest fixture.
#
# schemathesis.from_pytest_fixture("schema") creates a LazySchema with
# fixture_name="schema". At test collection time the schemathesis pytest
# plugin resolves that fixture name to the BaseSchema loaded in conftest.py
# (schemathesis.from_asgi("/openapi.json", fastapi_app)).
#
# .include(path_regex=r"^/api/v1/jobs") filters the schema to only the
# operations whose path matches the regex, keeping this file focused on the
# /api/v1/jobs* surface.
# ---------------------------------------------------------------------------
schema = schemathesis.from_pytest_fixture("schema").include(path_regex=r"^/api/v1/jobs")


# ---------------------------------------------------------------------------
# Auth + DB + lifespan setup fixture
#
# Installs the header-based fake auth override, seeds the test user row,
# and points app.db.engine at the SQLite test engine so the ASGI lifespan
# (triggered by case.call_asgi()) runs cleanly without a real Postgres DB.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _install_jobs_contract_auth(setup_db):
    """Install auth + DB overrides and seed the test user for jobs contract tests.

    setup_db dependency ensures the SQLite schema is created before we
    seed the user row. Runs per-test (function scope) because setup_db
    recreates the schema per test.
    """
    import app.db as _app_db
    import app.main as _app_main
    from app.auth.cf_access import cf_access_user as _cf_dep
    from app.db import get_async_session
    from app.main import app
    from app.models import Role, User
    from app.services.user_handle import derive_handle_from_email, next_unique_handle
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.conftest import test_engine, test_session_maker

    # --- Override the production engine with the test engine so that
    # _assert_schema_at_head() inside the ASGI lifespan reads from SQLite.
    # SQLite has no alembic_version table → the check returns early via
    # OperationalError catch, and no Postgres DNS lookup is attempted.
    #
    # NOTE: app.main does `from app.db import engine` which creates a
    # local binding. We must patch the `engine` name in app.main, not just
    # in app.db, for _assert_schema_at_head() to see the test engine.
    original_db_engine = _app_db.engine
    original_main_engine = _app_main.engine
    _app_db.engine = test_engine
    _app_main.engine = test_engine

    async def _override_session():
        async with test_session_maker() as session:
            yield session

    # Seed the test user in the current (freshly created) test DB.
    async with test_session_maker() as session:
        existing = (
            await session.execute(select(User).where(User.email == _TEST_USER_EMAIL))
        ).scalar_one_or_none()
        if existing is None:
            existing_handles = set(
                (await session.execute(select(User.handle))).scalars().all()
            )
            base_handle = derive_handle_from_email(_TEST_USER_EMAIL)
            handle = next_unique_handle(base_handle, existing=existing_handles)
            user = User(
                email=_TEST_USER_EMAIL,
                handle=handle,
                role=Role.USER,
                display_name="contract-jobs",
            )
            session.add(user)
            await session.commit()

    # Header-based fake auth: reads user email from X-Test-User-Email header.
    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email (contract fixture)")
        row = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"contract fixture: user not seeded: {email}")
        return row

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[_cf_dep] = _fake_auth

    yield

    app.dependency_overrides.pop(get_async_session, None)
    app.dependency_overrides.pop(_cf_dep, None)
    _app_db.engine = original_db_engine
    _app_main.engine = original_main_engine


# ---------------------------------------------------------------------------
# Contract test
#
# @schema.parametrize() expands into one pytest sub-test per (method, path)
# operation in the filtered schema. For each operation schemathesis generates
# up to 50 payloads via Hypothesis.
#
# Auth: X-Test-User-Email injected into every generated request so the
# header-based auth override resolves the test user instead of 401-ing.
#
# validate_response() checks:
# - response status code is in the OpenAPI schema for that operation
# - response body matches the declared response schema
#
# Operations exercised (from app/routers/jobs.py):
#   POST   /api/v1/jobs                       create_job
#   GET    /api/v1/jobs                       list_jobs
#   GET    /api/v1/jobs/{job_id}              get_job
#   GET    /api/v1/jobs/{job_id}/prediction-summary
#   GET    /api/v1/jobs/{job_id}/logs
#   GET    /api/v1/jobs/{job_id}/queue-position
#   POST   /api/v1/jobs/{job_id}/cancel       cancel_job
#   PATCH  /api/v1/jobs/{job_id}              patch_job
#   GET    /api/v1/jobs/{job_id}/events       list_job_events
#   WebSocket /api/v1/jobs/{job_id}/events   -- excluded (not in HTTP schema)
#
# Known schema gaps (xfail):
#   None at authoring time (2026-05-15). Add xfail markers here if drift is
#   discovered. Format:
#     @pytest.mark.xfail(reason="contract gap: <endpoint> returns <code> undocumented")
# ---------------------------------------------------------------------------


@schema.parametrize()
@h_settings(max_examples=50, deadline=None)
def test_jobs_endpoints_match_schema(case):
    """Every generated payload is either accepted per spec or rejected with a documented status code."""
    response = case.call(headers={"x-test-user-email": _TEST_USER_EMAIL})
    case.validate_response(response)
