"""E2E live-stack rate-limit Redis bootstrap (frontend-slow workflow).

The E2E webServer config sets AUTH_DEV_MODE=true and spawns uvicorn
without a Redis sidecar. The default REDIS_URL points at host `redis`,
which doesn't resolve in CI → `socket.gaierror` 500 on every
rate-limited request (including `/api/v1/health`).

`get_redis()` now substitutes `fakeredis.aioredis.FakeRedis` in dev
mode so the same code path that rate-limits in prod returns a working
in-process counter in dev. Production rejects AUTH_DEV_MODE=true at
boot via Settings.validate_sso_config, so this branch can never fire
there; the prod image also runs `uv sync --no-dev` so fakeredis is
absent — both layers protect against drift.
"""

from __future__ import annotations

import pytest
from app.services import rate_limit as rl_module


@pytest.fixture
def reset_module_redis(monkeypatch):
    """Reset the module-level _redis so get_redis() re-runs its bootstrap."""
    monkeypatch.setattr(rl_module, "_redis", None)
    yield


@pytest.mark.asyncio
async def test_get_redis_uses_fakeredis_when_dev_mode(monkeypatch, reset_module_redis):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)

    client = rl_module.get_redis()

    from fakeredis.aioredis import FakeRedis

    assert isinstance(client, FakeRedis)
    # Smoke-test: a round-trip works without a Redis daemon present.
    await client.set("k", "v")
    assert await client.get("k") == "v"


@pytest.mark.asyncio
async def test_get_redis_uses_real_url_when_dev_mode_off(
    monkeypatch, reset_module_redis
):
    """When AUTH_DEV_MODE=false, get_redis() must NOT return a FakeRedis.

    Constructing the real client doesn't open a socket (lazy connect on
    first command), so we can verify the type without a running Redis.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/0")

    client = rl_module.get_redis()

    from fakeredis.aioredis import FakeRedis

    assert not isinstance(client, FakeRedis)
