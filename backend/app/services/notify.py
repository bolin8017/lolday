"""Discord notification delivery for user-facing lolday events.

Each `notify_*` coroutine builds the embed payload and awaits
`post_webhook`. Internal failures (`httpx` errors, non-2xx Discord response)
are logged + counted into `BACKEND_ERRORS{stage="discord_notify"}` and
swallowed — never propagate to the caller.

Callers wrap in `asyncio.create_task(notify_*(...))` for fire-and-forget
semantics (see `app.reconciler`). A 5-second httpx timeout guards against
a slow Discord from pinning the task; since failures are swallowed, the
scheduled task always terminates cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.metrics import BACKEND_ERRORS, DISCORD_NOTIFY_TOTAL
from app.services.discord import (
    build_build_completed_embed,
    build_build_failed_embed,
    build_job_completed_embed,
    build_job_failed_embed,
    build_trivy_blocked_embed,
)

# Single channel label for the backend's only webhook path today
# (Spidey Service Alerts via DISCORD_WEBHOOK_URL_EVENTS). Promoted to a
# constant so future webhook paths (e.g. backend-driven critical alerts)
# can be added without scattering the label literal across call sites.
_NOTIFY_CHANNEL = "events"

logger = logging.getLogger(__name__)

# M-notify-semaphore (security-hardening P6): cap per-pod concurrent
# webhook posts. 20 permits x 2 backend replicas = up to 40 outbound at any
# moment; well below httpx default max_connections=100 and Discord's
# per-webhook rate limit (30/60s). See plan section D4 for sizing validation.
# Acquire is non-blocking -- exceeded permits drop the notify (counted
# in BACKEND_ERRORS{stage="discord_notify_dropped"}). The drop preserves
# fire-and-forget semantics: producers asyncio.create_task(notify_*())
# never block on this path.
_NOTIFY_SEM: asyncio.Semaphore = asyncio.Semaphore(20)


async def post_webhook(payload: dict) -> None:
    url = settings.DISCORD_WEBHOOK_URL_EVENTS
    if not url:
        return
    host = urlparse(url).hostname or "?"

    # M-notify-semaphore: non-blocking acquire. Drop if saturated to
    # preserve fire-and-forget semantics. asyncio.Semaphore.locked() is
    # the public API for "no permits available" -- _value access is
    # CPython internal and not needed here.
    if _NOTIFY_SEM.locked():
        BACKEND_ERRORS.labels(stage="discord_notify_dropped").inc()
        DISCORD_NOTIFY_TOTAL.labels(channel=_NOTIFY_CHANNEL, result="dropped").inc()
        logger.warning(
            "Discord notify dropped (semaphore saturated): host=%s",
            host,
        )
        return

    async with _NOTIFY_SEM:
        try:
            async with httpx.AsyncClient(
                timeout=settings.DISCORD_HTTP_TIMEOUT_SECONDS
            ) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            DISCORD_NOTIFY_TOTAL.labels(channel=_NOTIFY_CHANNEL, result="ok").inc()
        except httpx.HTTPStatusError as exc:
            BACKEND_ERRORS.labels(stage="discord_notify").inc()
            DISCORD_NOTIFY_TOTAL.labels(
                channel=_NOTIFY_CHANNEL, result="http_error"
            ).inc()
            # M-discord-log: webhook URL is itself the secret -- log host + status
            # only. Full path / token is the same value Discord uses to authenticate
            # the POST, so anything that lands in Loki is effectively the credential.
            logger.warning(
                "Discord notify failed: status=%s host=%s",
                exc.response.status_code,
                host,
            )
        except Exception as exc:
            BACKEND_ERRORS.labels(stage="discord_notify").inc()
            DISCORD_NOTIFY_TOTAL.labels(
                channel=_NOTIFY_CHANNEL, result="network_error"
            ).inc()
            logger.warning(
                "Discord notify failed: error=%s host=%s",
                type(exc).__name__,
                host,
            )


async def notify_job_completed(**kwargs) -> None:
    await post_webhook(build_job_completed_embed(**kwargs))


async def notify_job_failed(**kwargs) -> None:
    await post_webhook(build_job_failed_embed(**kwargs))


async def notify_build_completed(**kwargs) -> None:
    await post_webhook(build_build_completed_embed(**kwargs))


async def notify_build_failed(**kwargs) -> None:
    await post_webhook(build_build_failed_embed(**kwargs))


async def notify_trivy_blocked(**kwargs) -> None:
    await post_webhook(build_trivy_blocked_embed(**kwargs))
