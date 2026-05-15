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
fixture installs the overrides and seeds the test user row via the shared
``install_contract_auth`` helper in ``tests/contract/conftest.py``.

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

from tests.contract.conftest import install_contract_auth

pytestmark = [pytest.mark.contract, pytest.mark.timeout(180)]

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
# Delegates to install_contract_auth (contract/conftest.py) which patches
# both engine references, seeds the test user, and installs the header-based
# fake-auth override. Runs per-test (function scope) because setup_db
# recreates the schema per test.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _install_jobs_contract_auth(setup_db):
    """Install auth + DB overrides and seed the test user for jobs contract tests."""
    from app.models import Role

    async with install_contract_auth(_TEST_USER_EMAIL, Role.USER):
        yield


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
