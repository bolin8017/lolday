"""M-notify-semaphore: post_webhook drops when _NOTIFY_SEM is saturated."""

import pytest
from prometheus_client import REGISTRY


def _read(metric: str, **labels) -> float:
    v = REGISTRY.get_sample_value(metric, labels=labels)
    return 0.0 if v is None else v


async def test_notify_semaphore_drops_on_saturation(monkeypatch):
    """When all 20 permits are held, post_webhook drops + increments BACKEND_ERRORS{stage=discord_notify_dropped}
    AND DISCORD_NOTIFY_TOTAL{channel=events, result=dropped} (2026-05-17 audit follow-up #4)."""
    from app.services import notify

    sem = notify._NOTIFY_SEM
    # Precondition: the module-level semaphore has all 20 permits available.
    # Use the public API; the 20-acquire loop would also self-enforce this.
    assert not sem.locked(), "semaphore should have permits before saturation"
    for _ in range(20):
        await sem.acquire()

    before = _read("lolday_backend_errors_total", stage="discord_notify_dropped")
    before_drop = _read(
        "lolday_discord_notify_total", channel="events", result="dropped"
    )

    monkeypatch.setattr(
        notify.settings, "DISCORD_WEBHOOK_URL_EVENTS", "https://discord.test/x"
    )
    await notify.post_webhook({"content": "test"})

    after = _read("lolday_backend_errors_total", stage="discord_notify_dropped")
    after_drop = _read(
        "lolday_discord_notify_total", channel="events", result="dropped"
    )
    assert after - before == pytest.approx(1.0)
    # 2026-05-17 audit follow-up #4: outcome Counter also increments under
    # `result=dropped`. Sibling assertion to the BACKEND_ERRORS check above.
    assert after_drop - before_drop == pytest.approx(1.0)

    for _ in range(20):
        sem.release()


async def test_notify_semaphore_passes_through_when_available(monkeypatch):
    """When permits are available, post_webhook proceeds (no drop counter increment)."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services import notify

    before = _read("lolday_backend_errors_total", stage="discord_notify_dropped")

    monkeypatch.setattr(
        notify.settings, "DISCORD_WEBHOOK_URL_EVENTS", "https://discord.test/x"
    )

    async def fake_post(*a, **kw):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        post = AsyncMock(side_effect=fake_post)

    monkeypatch.setattr(notify.httpx, "AsyncClient", _C)

    await notify.post_webhook({"content": "test"})

    after = _read("lolday_backend_errors_total", stage="discord_notify_dropped")
    assert after == before
