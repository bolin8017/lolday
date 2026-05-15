"""D2.3 Task 10 — CSRF / Origin enforcement integration test.

Covers backend/app/middleware/csrf.py invariants:
- Origin matches Host (same-origin) → request proceeds
- Origin host differs from Host → 403 (state-changing methods only)
- Sec-Fetch-Site: same-origin with no Origin → request proceeds
- /api/v1/internal/* exempt (job-token authed; isolated on :8001)
- GET requests are never blocked (CSRF middleware ignores read methods)
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "host,origin,sec_fetch,expect_403",
    [
        # Same-origin via Origin header
        ("lolday.connlabai.com", "https://lolday.connlabai.com", None, False),
        # Cross-origin Origin → reject
        ("lolday.connlabai.com", "https://evil.example.com", None, True),
        # Same-origin via Sec-Fetch-Site (no Origin)
        ("lolday.connlabai.com", None, "same-origin", False),
        # Both absent → CSRF middleware fails open (CLI / service-token traffic)
        ("lolday.connlabai.com", None, None, False),
    ],
)
@pytest.mark.asyncio
async def test_post_csrf_origin_invariant(
    auth_client_admin,
    host: str,
    origin: str | None,
    sec_fetch: str | None,
    expect_403: bool,
) -> None:
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    if sec_fetch:
        headers["Sec-Fetch-Site"] = sec_fetch
    r = await auth_client_admin.post("/api/v1/jobs", json={}, headers=headers)
    if expect_403:
        assert r.status_code == 403, (
            f"cross-origin POST should 403 but got {r.status_code}: {r.text[:200]}"
        )
    else:
        # Same-origin: the request reaches the validator and gets 422 (empty body)
        # rather than 403. Both 422 and 400 are acceptable — what matters is that
        # the request was NOT rejected by CSRF.
        assert r.status_code != 403, (
            f"same-origin POST blocked by CSRF: {r.status_code} {r.text[:200]}"
        )


@pytest.mark.asyncio
async def test_internal_path_exempt_from_csrf(auth_client_admin) -> None:
    """``/api/v1/internal/*`` is documented as CSRF-exempt (job-token authed,
    isolated on the internal port). Cross-origin POST must not 403."""
    r = await auth_client_admin.post(
        "/api/v1/internal/events/heartbeat",
        json={},
        headers={
            "Host": "lolday.connlabai.com",
            "Origin": "https://evil.example.com",
        },
    )
    assert r.status_code != 403, (
        f"internal path 403'd on cross-origin Origin: {r.text[:200]}"
    )


@pytest.mark.asyncio
async def test_get_never_blocked_by_csrf(auth_client_admin) -> None:
    """GET methods bypass CSRF middleware entirely — even with hostile Origin."""
    r = await auth_client_admin.get(
        "/api/v1/users/me",
        headers={
            "Host": "lolday.connlabai.com",
            "Origin": "https://evil.example.com",
        },
    )
    assert r.status_code != 403, f"GET blocked by CSRF: {r.text[:200]}"
