"""Discord notification delivery for user-facing lolday events.

Fire-and-forget semantics: failures are logged + counted into
`BACKEND_ERRORS{stage="discord_notify"}` but never propagate. Callers schedule
these via `asyncio.create_task(...)` so the request/reconciler path doesn't
block on Discord.
"""

from __future__ import annotations

import logging

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
    try:
        async with httpx.AsyncClient(timeout=settings.DISCORD_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception:
        BACKEND_ERRORS.labels(stage="discord_notify").inc()
        logger.exception("Discord webhook delivery failed")


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
