"""H-26: /health is IP-rate-limited at 120/60s; the 121st hit from a single IP returns 429."""

from unittest.mock import AsyncMock, patch


async def test_health_is_rate_limited_at_121st_hit_per_ip(client):
    """The dep returns 429 after 120 hits from the same IP in a window."""
    call_count = {"n": 0}

    async def fake_check_rate(key, limit, window_seconds):
        call_count["n"] += 1
        assert limit == 120
        assert window_seconds == 60
        assert key.startswith("rl:health:")
        return call_count["n"] <= 120

    with patch(
        "app.services.rate_limit.check_rate", new=AsyncMock(side_effect=fake_check_rate)
    ):
        for _ in range(120):
            r = await client.get("/api/v1/health")
            assert r.status_code == 200, r.text
        r = await client.get("/api/v1/health")
        assert r.status_code == 429


async def test_health_still_returns_ok_under_cap(client):
    """A single GET /health returns 200 + {'status':'ok'} (no rate-limit interference)."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
