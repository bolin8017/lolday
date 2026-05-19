"""Unit tests for the helper + branch contract in
``app.middleware.csrf``.

The integration tests under ``tests/integration/routers/test_csrf_middleware.py``
exercise the dispatch path end-to-end, but four branches stayed dead in
coverage:

- ``origin_matches_host`` lines 52-53: ``urlparse`` raising an exception
  (extremely rare — urlparse is permissive — but defensively caught).
- Line 55: an Origin string that parses but has an empty netloc.
- Lines 63 and 65: the symmetric ``host``-side default-port strip.
  Existing tests cover the Origin-side strip (``http://x:80`` matches
  ``x``) but never the inverse direction (``http://x`` matches ``x:80``).
- Dispatch line 82: a state-changing request OUTSIDE ``/api/v1/*``
  short-circuits past the Origin / Sec-Fetch-Site gate (the middleware
  only guards the API surface).

This file lives at the unit tier so it doesn't pull the integration
autouse fixtures (DB, Redis, K8s stubs); the contract under test is
pure-Python.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from app.middleware.csrf import CSRFOriginMiddleware, origin_matches_host
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

# ----------------------------------------------------------------------
# origin_matches_host helper
# ----------------------------------------------------------------------


def test_origin_matches_host_returns_false_when_urlparse_raises() -> None:
    """The defensive ``try/except`` around ``urlparse`` (lines 52-53):
    if a future urlparse swap raises on a pathological input the helper
    must return False, not propagate."""
    with patch("app.middleware.csrf.urlparse", side_effect=ValueError("boom")):
        assert origin_matches_host("http://example.com", "example.com") is False


def test_origin_matches_host_returns_false_for_empty_netloc() -> None:
    """An Origin that parses but yields an empty netloc (line 55) — e.g.
    a path-only string like ``/api`` — must not be matched, otherwise a
    rogue ``Origin: /`` could spoof same-host."""
    assert origin_matches_host("/api/v1", "example.com") is False
    # Same with a literal empty string.
    assert origin_matches_host("", "example.com") is False


def test_origin_matches_host_strips_default_port_from_host_http() -> None:
    """Line 63: when the Origin omits the default :80 but the Host
    declares it, the helper must still match. Mirrors the inverse case
    already covered by `test_csrf_post_origin_default_port_80_matches_host`."""
    assert origin_matches_host("http://testserver", "testserver:80") is True


def test_origin_matches_host_strips_default_port_from_host_https() -> None:
    """Line 65: symmetric :443 strip for https."""
    assert origin_matches_host("https://testserver", "testserver:443") is True


def test_origin_matches_host_does_not_strip_non_default_port() -> None:
    """Sanity: only :80/:443 are stripped, never an arbitrary port."""
    assert origin_matches_host("http://testserver", "testserver:8080") is False


# ----------------------------------------------------------------------
# Dispatch: non-/api/v1/* state-changing request bypasses the gate
# ----------------------------------------------------------------------


@pytest.fixture
def app_with_csrf_middleware_and_root_post() -> Starlette:
    """A bare Starlette app with a POST route OUTSIDE /api/v1/*.

    The CSRF middleware only guards /api/v1/*. Without a non-/api route
    in tests, line 82 (the early return for paths outside the gated
    prefix) stayed dead — every prior test was inside /api/v1/*.
    """

    async def root(_request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/healthz", root, methods=["POST"])])
    app.add_middleware(CSRFOriginMiddleware)
    return app


def test_dispatch_bypasses_csrf_for_non_api_v1_state_changing_path(
    app_with_csrf_middleware_and_root_post: Starlette,
) -> None:
    """A POST to a non-/api/v1 path skips the Sec-Fetch-Site / Origin
    check entirely (line 82). Without this branch the middleware would
    incorrectly 403 things like a health-check POST.

    Send a Sec-Fetch-Site header that WOULD be rejected inside
    /api/v1/* (``cross-site``) — the response should still be 200,
    proving the prefix gate fired first.
    """
    client = TestClient(app_with_csrf_middleware_and_root_post)
    r = client.post("/healthz", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
