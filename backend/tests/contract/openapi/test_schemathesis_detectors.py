"""Schemathesis property-based contract test for /api/v1/detectors endpoints.

Schemathesis enumerates every operation matching /api/v1/detectors* in the
OpenAPI document FastAPI generates from the route definitions and
auto-generates up to 50 cases per operation (boundary values, nullables,
unicode strings, integer overflow, etc.). Each case is sent against the
running ASGI app and the response is validated against the documented
response schema.

This test catches:
- OpenAPI <-> actual response drift (e.g. handler returns a field the
  schema doesn't declare)
- Validation regressions (handler returns 500 where it should return 422)
- Status-code documentation gaps (handler returns 409 with no schema entry)

Auth setup: the test user is seeded with Role.DEVELOPER so schemathesis can
reach every route guard without hitting a 403 on the role check. The shared
``install_contract_auth`` helper in ``tests/contract/conftest.py`` handles
engine patching, user seeding, and header-based auth override.

Note on endpoints that call external services:
- POST /api/v1/detectors (register) — validates git_url shape and then
  calls GitHub API + clones. Schemathesis generates random git_url strings
  that fail normalize_git_url() → 422, or fail the GitHub pre-flight → 400.
  Both 400 and 422 are documented FastAPI response codes (422 from Pydantic
  validation, 400 from explicit HTTPException). No network calls succeed.
- POST /api/v1/detectors/{id}/builds — requires PAT in DB; test user has no
  PAT → handler returns 400 (credential_missing). Documented via the 400
  status code entry in the OpenAPI schema.
- GET /api/v1/detectors/{id}/available-tags — also requires owner access +
  calls GitHub API. Random detector_id → 404. Documented.

Operations exercised (from app/routers/detectors.py):
  POST   /api/v1/detectors                               register
  GET    /api/v1/detectors                               list_detectors
  GET    /api/v1/detectors/{detector_id}                 get_detector
  PATCH  /api/v1/detectors/{detector_id}                 update_detector
  DELETE /api/v1/detectors/{detector_id}                 delete_detector
  GET    /api/v1/detectors/{detector_id}/versions        list_versions
  GET    /api/v1/detectors/{detector_id}/versions/{tag}  get_version
  DELETE /api/v1/detectors/{detector_id}/versions/{tag}  delete_version
  GET    /api/v1/detectors/{detector_id}/available-tags  available_tags
  POST   /api/v1/detectors/{detector_id}/builds          create_build
  GET    /api/v1/detectors/{detector_id}/builds          list_builds
  GET    /api/v1/detectors/{detector_id}/builds/{build_id}       get_build
  POST   /api/v1/detectors/{detector_id}/builds/{build_id}/cancel cancel_build

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
D1.7 part 2/5.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import schemathesis
from hypothesis import settings as h_settings

from tests.contract.conftest import install_contract_auth

pytestmark = pytest.mark.contract

_TEST_USER_EMAIL = "contract-detectors@example.dev"

# ---------------------------------------------------------------------------
# LazySchema bound to the "schema" conftest fixture.
#
# .include(path_regex=...) filters to /api/v1/detectors* operations only,
# keeping this file focused on the detectors surface.
# ---------------------------------------------------------------------------
schema = schemathesis.from_pytest_fixture("schema").include(
    path_regex=r"^/api/v1/detectors"
)


# ---------------------------------------------------------------------------
# Auth + DB + lifespan setup fixture
#
# Role.DEVELOPER is required for POST /api/v1/detectors (register). Using
# DEVELOPER ensures the test user clears the role guard on every endpoint;
# random path-param values (detector_id, build_id) will still produce 404s
# for the parameterised sub-routes, which are documented response codes.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _install_detectors_contract_auth(setup_db):
    """Install auth + DB overrides and seed a DEVELOPER user for detectors contract tests."""
    from app.models import Role

    async with install_contract_auth(_TEST_USER_EMAIL, Role.DEVELOPER):
        yield


# ---------------------------------------------------------------------------
# Contract test
#
# @schema.parametrize() expands into one pytest sub-test per (method, path)
# operation in the filtered schema. For each operation schemathesis generates
# up to 50 payloads via Hypothesis.
#
# Auth: X-Test-User-Email injected into every generated request so the
# header-based auth override resolves the DEVELOPER test user.
#
# validate_response() checks:
# - response status code is in the OpenAPI schema for that operation
# - response body matches the declared response schema
#
# Known schema gaps (xfail):
#   None at authoring time (2026-05-15). Add xfail markers here if drift is
#   discovered. Format:
#     @pytest.mark.xfail(reason="contract gap: <endpoint> returns <code> undocumented")
# ---------------------------------------------------------------------------


@schema.parametrize()
@h_settings(max_examples=50, deadline=None)
def test_detectors_endpoints_match_schema(case):
    """Every generated payload is either accepted per spec or rejected with a documented status code."""
    response = case.call(headers={"x-test-user-email": _TEST_USER_EMAIL})
    case.validate_response(response)
