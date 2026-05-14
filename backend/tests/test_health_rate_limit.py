"""H-26: /health is IP-rate-limited at 120/60s; the 121st hit from a single IP returns 429."""

from unittest.mock import AsyncMock, patch

import pytest  # noqa: F401  # convention: tests import pytest


async def test_health_is_rate_limited_at_121st_hit_per_ip(client):
    """The dep returns 429 after 120 hits from the same IP in a window."""
    captured_calls = []

    async def fake_check_rate(key, limit, window_seconds):
        captured_calls.append((key, limit, window_seconds))
        return len(captured_calls) <= 120

    with patch(
        "app.services.rate_limit.check_rate",
        new=AsyncMock(side_effect=fake_check_rate),
    ):
        for _ in range(120):
            r = await client.get("/api/v1/health")
            assert r.status_code == 200, r.text
        r = await client.get("/api/v1/health")
        assert r.status_code == 429

    # Parameter-shape contract — asserted outside the mock context so failures
    # surface cleanly under pytest.
    assert all(lim == 120 for _, lim, _ in captured_calls)
    assert all(ws == 60 for _, _, ws in captured_calls)
    assert all(k.startswith("rl:health:") for k, _, _ in captured_calls)


async def test_health_still_returns_ok_under_cap(client):
    """A single GET /health returns 200 + {'status':'ok'} (no rate-limit interference)."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
