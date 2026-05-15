"""Tests for app.services.notify — Discord webhook delivery layer."""

import io
import logging

import httpx
import pytest
import respx
from app.metrics import BACKEND_ERRORS
from app.services import notify

WEBHOOK = "https://discord.test/api/webhooks/1/xyz"


def _notify_error_count() -> float:
    """Sample of current BACKEND_ERRORS{stage=discord_notify} value."""
    return BACKEND_ERRORS.labels(stage="discord_notify")._value.get()


@pytest.mark.asyncio
async def test_post_webhook_noop_when_url_not_configured(monkeypatch):
    monkeypatch.setattr("app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", "")
    # No respx mock — a network hit would be AllMockedAssertion in normal usage.
    # Call should silently return None.
    result = await notify.post_webhook({"content": "hi"})
    assert result is None


@pytest.mark.asyncio
async def test_post_webhook_posts_json_to_configured_url(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    with respx.mock() as mock:
        route = mock.post(WEBHOOK).mock(return_value=httpx.Response(204))
        await notify.post_webhook({"content": "hi"})
        assert route.called
        sent = route.calls.last.request
        assert sent.headers["content-type"].startswith("application/json")
        assert b'"content":"hi"' in sent.content


@pytest.mark.asyncio
async def test_post_webhook_swallows_http_error_and_increments_metric(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    before = _notify_error_count()
    with respx.mock() as mock:
        mock.post(WEBHOOK).mock(return_value=httpx.Response(500))
        await notify.post_webhook({"content": "hi"})  # must not raise
    after = _notify_error_count()
    assert after == before + 1


@pytest.mark.asyncio
async def test_post_webhook_swallows_network_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    before = _notify_error_count()
    with respx.mock() as mock:
        mock.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
        await notify.post_webhook({"content": "hi"})  # must not raise
    after = _notify_error_count()
    assert after == before + 1


@pytest.mark.asyncio
async def test_notify_job_completed_builds_and_posts(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    with respx.mock() as mock:
        route = mock.post(WEBHOOK).mock(return_value=httpx.Response(204))
        await notify.notify_job_completed(
            user_name="alice",
            user_discord_id=None,
            job_type="train",
            detector_label="upxelfdet v0.5.0",
            dataset_name="ds",
            duration_seconds=1,
            primary_metric=("f1", 0.9),
            job_url="u",
            mlflow_url=None,
        )
        assert route.called
        payload = route.calls.last.request.content
        assert b"Job train completed" in payload
        assert b"**@alice**" in payload


@pytest.mark.asyncio
async def test_notify_job_failed_sends_red_embed(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    with respx.mock() as mock:
        route = mock.post(WEBHOOK).mock(return_value=httpx.Response(204))
        await notify.notify_job_failed(
            user_name="alice",
            user_discord_id=None,
            job_type="train",
            detector_label="d",
            dataset_name=None,
            failure_reason="oom",
            job_url="u",
        )
        assert route.called
        assert b"Job train failed" in route.calls.last.request.content


@pytest.mark.asyncio
async def test_notify_trivy_blocked_sends_orange_embed(monkeypatch):
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )
    with respx.mock() as mock:
        route = mock.post(WEBHOOK).mock(return_value=httpx.Response(204))
        await notify.notify_trivy_blocked(
            user_name="alice",
            user_discord_id=None,
            detector_label="d",
            git_tag="v1",
            cve_summary="2 critical",
            build_url="u",
        )
        assert route.called
        assert b"Trivy blocked" in route.calls.last.request.content


@pytest.mark.asyncio
async def test_post_webhook_500_logs_host_not_url(monkeypatch):
    """M-discord-log: failure log must contain host + status, never the
    webhook token or path. Attach a fresh handler directly to the notify
    logger so capture is independent of pytest caplog's ordering behaviour
    in the full suite.

    Also re-enables the logger for the test duration: the Alembic migration
    test fixture calls logging.config.fileConfig(alembic.ini) which sets
    disable_existing_loggers=True by default, marking all pre-existing
    loggers (including app.services.notify) as disabled.
    """
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    notify_logger = logging.getLogger("app.services.notify")
    notify_logger.addHandler(handler)
    prev_level = notify_logger.level
    prev_disabled = notify_logger.disabled
    notify_logger.setLevel(logging.WARNING)
    # logging.config.fileConfig (called by the alembic migrations test fixture)
    # sets disable_existing_loggers=True by default, which marks all pre-existing
    # loggers as disabled. Re-enable this specific logger for the duration of the
    # test so our handler can receive records regardless of test ordering.
    notify_logger.disabled = False
    try:
        with respx.mock() as mock:
            mock.post(WEBHOOK).mock(return_value=httpx.Response(500))
            await notify.post_webhook({"content": "hi"})
    finally:
        notify_logger.disabled = prev_disabled
        notify_logger.removeHandler(handler)
        notify_logger.setLevel(prev_level)

    messages = buf.getvalue()
    # Token + path are the secret part of the URL.
    assert "xyz" not in messages
    assert "/api/webhooks/" not in messages
    # Host + status are useful for ops debug.
    assert "discord.test" in messages
    assert "status=500" in messages


@pytest.mark.asyncio
async def test_post_webhook_network_error_logs_host_not_url(monkeypatch):
    """A ConnectError carries the URL in its repr — make sure we don't leak it.
    Handler-attach pattern same as above; independent of pytest caplog.
    Also re-enables the notify logger in case a prior migration test's
    fileConfig(disable_existing_loggers=True) call disabled it.
    """
    monkeypatch.setattr(
        "app.services.notify.settings.DISCORD_WEBHOOK_URL_EVENTS", WEBHOOK
    )

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(message)s"))
    notify_logger = logging.getLogger("app.services.notify")
    notify_logger.addHandler(handler)
    prev_level = notify_logger.level
    prev_disabled = notify_logger.disabled
    notify_logger.setLevel(logging.WARNING)
    notify_logger.disabled = False
    try:
        with respx.mock() as mock:
            mock.post(WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
            await notify.post_webhook({"content": "hi"})
    finally:
        notify_logger.disabled = prev_disabled
        notify_logger.removeHandler(handler)
        notify_logger.setLevel(prev_level)

    messages = buf.getvalue()
    assert "xyz" not in messages
    assert "/api/webhooks/" not in messages
    assert "discord.test" in messages
    assert "error=ConnectError" in messages
