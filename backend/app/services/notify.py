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

import logging
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.services.discord import (
    build_build_completed_embed,
    build_build_failed_embed,
    build_job_completed_embed,
    build_job_failed_embed,
    build_trivy_blocked_embed,
)

logger = logging.getLogger(__name__)


async def post_webhook(payload: dict) -> None:
    url = settings.DISCORD_WEBHOOK_URL_EVENTS
    if not url:
        return
    host = urlparse(url).hostname or "?"
    try:
        async with httpx.AsyncClient(
            timeout=settings.DISCORD_HTTP_TIMEOUT_SECONDS
        ) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        BACKEND_ERRORS.labels(stage="discord_notify").inc()
        # M-discord-log: webhook URL is itself the secret — log host + status
        # only. Full path / token is the same value Discord uses to authenticate
        # the POST, so anything that lands in Loki is effectively the credential.
        logger.warning(
            "Discord notify failed: status=%s host=%s",
            exc.response.status_code,
            host,
        )
    except Exception as exc:
        BACKEND_ERRORS.labels(stage="discord_notify").inc()
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
