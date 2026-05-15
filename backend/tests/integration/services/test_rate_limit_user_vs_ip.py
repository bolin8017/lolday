"""D2.3 Task 11 — rate-limit keying invariants: per-user vs per-IP.

app/services/rate_limit.py exposes two limiters:
- ``rate_limit_user``: keyed on ``rl:{prefix}:{user.id}`` — bucket follows
  the user across IPs (two NATs share the same bucket).
- ``rate_limit_ip``: keyed on ``rl:{prefix}:{request.client.host}`` —
  bucket follows the IP across users (two users behind one NAT share the
  same bucket).

The bug class to catch: a route accidentally swaps the wrong limiter and
the wrong subjects collide. Tests exercise the helpers directly because
the end-to-end POST /jobs flow includes lots of unrelated machinery.
"""

from __future__ import annotations

import uuid

import pytest
from app.models import User
from app.models.user import Role
from app.services import rate_limit as rl_module
from fakeredis.aioredis import FakeRedis


@pytest.fixture
async def fake_redis(monkeypatch):
    """Swap the module-level redis client for a fakeredis instance per test."""
    client = FakeRedis(decode_responses=True)
    monkeypatch.setattr(rl_module, "_redis", client)
    yield client
    await client.flushall()
    await client.aclose()


def _user(email: str = "u1@example.dev") -> User:
    return User(id=uuid.uuid4(), email=email, role=Role.DEVELOPER, handle="u1")


@pytest.mark.asyncio
async def test_rate_limit_user_keys_on_user_id(fake_redis) -> None:
    """rate_limit_user's bucket key contains the user id — not the client IP."""
    dep = rl_module.rate_limit_user("test", limit=3, window_seconds=60)

    user_a = _user("a@example.dev")
    user_b = _user("b@example.dev")

    # Saturate user_a's bucket.
    for _ in range(3):
        await dep(user_a)
    # 4th call for user_a → 429
    with pytest.raises(Exception) as exc:
        await dep(user_a)
    assert getattr(exc.value, "status_code", None) == 429

    # user_b on the same Redis must still be allowed (different user_id).
    await dep(user_b)


@pytest.mark.asyncio
async def test_rate_limit_user_bucket_persists_across_ip(fake_redis) -> None:
    """Same user from two IPs shares one bucket (key carries no IP). We
    cannot directly inject an IP into rate_limit_user since it ignores
    ``request.client.host`` — proving the invariant is exactly that absence.

    Asserted by: same user_id → same Redis key → same counter."""
    dep = rl_module.rate_limit_user("test", limit=2, window_seconds=60)
    user = _user("c@example.dev")

    await dep(user)
    await dep(user)
    # 3rd hit → 429 regardless of any IP context
    with pytest.raises(Exception) as exc:
        await dep(user)
    assert getattr(exc.value, "status_code", None) == 429

    # Inspect the actual Redis key to lock the contract.
    key = f"rl:test:{user.id}"
    val = await fake_redis.get(key)
    assert val is not None and int(val) >= 3


@pytest.mark.asyncio
async def test_rate_limit_ip_keys_on_client_host(fake_redis) -> None:
    """rate_limit_ip's key is the request.client.host, not the user."""

    dep = rl_module.rate_limit_ip("login", limit=2, window_seconds=60)

    class _FakeClient:
        host = "10.0.0.42"

    class _FakeReq:
        client = _FakeClient()

    req = _FakeReq()  # type: ignore[assignment]  # adequate quack for the helper

    await dep(req)
    await dep(req)
    with pytest.raises(Exception) as exc:
        await dep(req)
    assert getattr(exc.value, "status_code", None) == 429

    key = f"rl:login:{_FakeClient.host}"
    val = await fake_redis.get(key)
    assert int(val) >= 3
