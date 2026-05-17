"""Redis-backed fixed-window rate limiter.

Backs three call sites:
- POST /api/v1/auth/login (IP-keyed, via middleware)
- POST /api/v1/jobs (user-keyed dependency)
- POST /api/v1/detectors/{id}/builds (user-keyed dependency)

Fixed-window semantics: INCR the bucket key; first hit installs a TTL; the
N-th hit (where N > limit) returns 429. Coarser than sliding window but
simpler and correct across the 2-replica backend Deployment because the
Redis counter is shared.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis, from_url

from app.config import settings
from app.metrics import RATE_LIMIT_HITS_TOTAL
from app.models import User
from app.users import current_active_user

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        if settings.AUTH_DEV_MODE:
            # E2E live-stack and local dev runs (AUTH_DEV_MODE=true) don't
            # ship a Redis sidecar. fakeredis is a dev dep, present because
            # uv sync includes the dev group in dev environments. Production
            # rejects AUTH_DEV_MODE=true at boot via validate_sso_config, so
            # this branch can never fire there — and fakeredis stays out of
            # the prod image (uv sync --no-dev in the Dockerfile).
            from fakeredis.aioredis import FakeRedis

            _redis = FakeRedis(decode_responses=True)
        else:
            _redis = from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def check_rate(key: str, limit: int, window_seconds: int) -> bool:
    r = get_redis()
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window_seconds)
    return count <= limit


def rate_limit_user(prefix: str, limit: int, window_seconds: int):
    async def _dep(user: User = Depends(current_active_user)) -> None:
        if not await check_rate(f"rl:{prefix}:{user.id}", limit, window_seconds):
            RATE_LIMIT_HITS_TOTAL.labels(prefix=prefix).inc()
            raise HTTPException(status_code=429, detail="rate limited")

    return _dep


def rate_limit_ip(prefix: str, limit: int, window_seconds: int):
    async def _dep(request: Request) -> None:
        if request.client is None:
            # No client address = misconfigured proxy or malformed request;
            # reject rather than bucket everyone under "unknown" (that shared
            # bucket is trivially DoS-able).
            # Do NOT increment RATE_LIMIT_HITS_TOTAL here — this 400 is a
            # proxy-misconfig reject, not a rate-limit overflow. The counter
            # is reserved for actual 429 outcomes (see ratelimit-metric).
            raise HTTPException(status_code=400, detail="client address required")
        ip = request.client.host
        if not await check_rate(f"rl:{prefix}:{ip}", limit, window_seconds):
            RATE_LIMIT_HITS_TOTAL.labels(prefix=prefix).inc()
            raise HTTPException(status_code=429, detail="rate limited")

    return _dep
