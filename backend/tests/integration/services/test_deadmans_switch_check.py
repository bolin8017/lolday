"""Unit tests for the Dead Man's Switch check script.

The script physically lives in the chart tree so helm can bake it into
a ConfigMap (see charts/lolday/templates/monitoring/deadmans-switch.yaml);
we load it here via importlib so the parse / retry / env-var-missing
paths get real unit coverage without a running cluster.

Regression paths deliberately covered:
  - Alertmanager unreachable (URLError) → failure string
  - Alertmanager returns [] → failure string (Watchdog missing)
  - Alertmanager returns non-list → failure string (malformed response)
  - Watchdog present, updatedAt fresh → None
  - Watchdog present, updatedAt stale → failure string with age
  - Watchdog.updatedAt unparseable → failure string, does not crash
  - DISCORD_URL missing → RuntimeError (fail-fast, CrashLoopBackOff)
  - Discord 429 → retry with Retry-After
  - Discord 5xx → retry with exponential backoff
  - Discord 4xx (non-429) → no retry, raise immediately
  - Discord network error → retry then re-raise
  - Discord success on first try → no retry
"""

from __future__ import annotations

import importlib.util
import io
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4]
    / "charts"
    / "lolday"
    / "files"
    / "deadmans_switch"
    / "check.py"
)


def _load_check_module():
    spec = importlib.util.spec_from_file_location("dms_check", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check_mod():
    return _load_check_module()


def _watchdog(updated_at: datetime) -> dict:
    return {
        "labels": {"alertname": "Watchdog", "severity": "none"},
        "startsAt": "2026-04-01T00:00:00.000Z",
        "updatedAt": updated_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "status": {"state": "active"},
    }


# --- check() --------------------------------------------------------------


def test_check_alertmanager_unreachable_returns_failure_string(check_mod):
    with mock.patch.object(
        check_mod,
        "fetch_alerts",
        side_effect=urllib.error.URLError("Name does not resolve"),
    ):
        reason = check_mod.check()
    assert reason is not None
    assert "Alertmanager unreachable" in reason
    assert "URLError" in reason


def test_check_watchdog_missing_returns_failure_string(check_mod):
    with mock.patch.object(check_mod, "fetch_alerts", return_value=[]):
        reason = check_mod.check()
    assert reason is not None
    assert "Watchdog" in reason
    assert "not present" in reason


def test_check_malformed_response_returns_failure_string(check_mod):
    """Alertmanager should always return a list; anything else is broken."""
    with mock.patch.object(check_mod, "fetch_alerts", return_value={"oops": True}):
        reason = check_mod.check()
    assert reason is not None
    assert "not a list" in reason


def test_check_watchdog_fresh_returns_none(check_mod):
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)
    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(fresh)]):
        reason = check_mod.check(now=now)
    assert reason is None


def test_check_watchdog_stale_returns_age_in_failure_string(check_mod):
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=check_mod.MAX_AGE_SECONDS + 60)
    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(stale)]):
        reason = check_mod.check(now=now)
    assert reason is not None
    assert "stopped sending" in reason
    # The age should be in the message so the operator can see how far
    # behind we are without re-querying Alertmanager themselves.
    assert f"{check_mod.MAX_AGE_SECONDS}s" in reason


def test_check_watchdog_unparseable_updated_at_returns_failure_string(check_mod):
    now = datetime.now(UTC)
    watchdog = {
        "labels": {"alertname": "Watchdog"},
        "updatedAt": "not-a-valid-iso-date",
    }
    with mock.patch.object(check_mod, "fetch_alerts", return_value=[watchdog]):
        reason = check_mod.check(now=now)
    assert reason is not None
    assert "unparseable" in reason


# --- alert_discord() ------------------------------------------------------


def test_alert_discord_missing_env_raises_runtimeerror(check_mod, monkeypatch):
    """Missing DISCORD_URL must fail-fast → CrashLoopBackOff is observable
    via KubePodCrashLooping, whereas a swallowed KeyError looks like the
    switch is working forever."""
    monkeypatch.delenv("DISCORD_URL", raising=False)
    with pytest.raises(RuntimeError, match="DISCORD_URL env var missing"):
        check_mod.alert_discord("test reason")


def _fake_http_error(code, headers=None, body=b""):
    return urllib.error.HTTPError(
        url="http://discord.test",
        code=code,
        msg=f"HTTP {code}",
        hdrs=headers or {},
        fp=io.BytesIO(body),
    )


class _SuccessResp:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_discord_post_retries_on_429_with_retry_after(check_mod, monkeypatch):
    monkeypatch.setenv("DISCORD_URL", "http://discord.test/webhooks/x/y")
    attempts = [
        _fake_http_error(429, headers={"Retry-After": "0.5"}),
        _fake_http_error(429, headers={"Retry-After": "0.5"}),
        _SuccessResp(),
    ]

    def opener(req, timeout):
        result = attempts.pop(0)
        if isinstance(result, urllib.error.HTTPError):
            raise result
        return result

    sleeps: list[float] = []
    check_mod.alert_discord("test", opener=opener, sleep=sleeps.append)
    assert sleeps == [0.5, 0.5]
    assert attempts == []


def test_discord_post_retries_on_5xx_with_exp_backoff(check_mod, monkeypatch):
    monkeypatch.setenv("DISCORD_URL", "http://discord.test/webhooks/x/y")
    attempts = [
        _fake_http_error(500),
        _fake_http_error(502),
        _SuccessResp(),
    ]

    def opener(req, timeout):
        result = attempts.pop(0)
        if isinstance(result, urllib.error.HTTPError):
            raise result
        return result

    sleeps: list[float] = []
    check_mod.alert_discord("test", opener=opener, sleep=sleeps.append)
    # parseRetryAfter returns 1.0 when header absent → first sleep = 1.0
    # Second attempt also 5xx with no header → 1.0 again.
    assert sleeps == [1.0, 1.0]
    assert attempts == []


def test_discord_post_raises_immediately_on_4xx_non_429(check_mod, monkeypatch):
    """A 403 (bad webhook URL) is a config bug, not a transient network
    issue — retrying just delays the operator noticing."""
    monkeypatch.setenv("DISCORD_URL", "http://discord.test/webhooks/x/y")

    def opener(req, timeout):
        raise _fake_http_error(403)

    sleeps: list[float] = []
    with pytest.raises(urllib.error.HTTPError) as info:
        check_mod.alert_discord("test", opener=opener, sleep=sleeps.append)
    assert info.value.code == 403
    assert sleeps == []  # no retry


def test_discord_post_retries_on_network_error_then_raises(check_mod, monkeypatch):
    monkeypatch.setenv("DISCORD_URL", "http://discord.test/webhooks/x/y")

    def opener(req, timeout):
        raise urllib.error.URLError("transient DNS fail")

    sleeps: list[float] = []
    with pytest.raises(urllib.error.URLError):
        check_mod.alert_discord("test", opener=opener, sleep=sleeps.append)
    # MAX_ATTEMPTS=3 → 2 retries before final raise → 2 backoff sleeps.
    assert len(sleeps) == check_mod.DISCORD_MAX_ATTEMPTS - 1


def test_discord_post_success_on_first_try_no_retry(check_mod, monkeypatch):
    monkeypatch.setenv("DISCORD_URL", "http://discord.test/webhooks/x/y")
    called: list[None] = []

    def opener(req, timeout):
        called.append(None)
        return _SuccessResp()

    sleeps: list[float] = []
    check_mod.alert_discord("test", opener=opener, sleep=sleeps.append)
    assert len(called) == 1
    assert sleeps == []


def test_payload_carries_required_discord_shape(check_mod, monkeypatch):
    """Regression guard: the Discord webhook API requires `content` OR
    `embeds`. A refactor that drops `@here` from content would still
    let the message through; a refactor that drops embeds entirely
    would drop the specific failure context. We check both.
    """
    now = datetime(2026, 4, 22, 12, 34, 56, tzinfo=UTC)
    monkeypatch.setenv("CLUSTER_NAME", "lolday-unit-test")
    payload = check_mod._build_payload("some failure reason", now=now)
    assert payload["content"] == "@here"
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert "Dead Man's Switch" in embed["title"]
    assert embed["description"] == "some failure reason"
    field_by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert field_by_name["Cluster"] == "lolday-unit-test"
    assert field_by_name["Timestamp"] == "2026-04-22T12:34:56+00:00"


# --- positive heartbeat (Phase 11) ----------------------------------------


def test_heartbeat_payload_is_green_with_age_and_timestamp(check_mod, monkeypatch):
    """The success embed must (a) NOT carry `@here` (no notification ping
    for healthy state) and (b) include the Watchdog age so an observer can
    sanity-check the chain at a glance."""
    now = datetime(2026, 5, 16, 22, 0, 0, tzinfo=UTC)
    monkeypatch.setenv("CLUSTER_NAME", "lolday-unit-test")
    payload = check_mod._build_heartbeat_payload(age_seconds=42, now=now)
    assert "content" not in payload, "heartbeat must not @here-ping"
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert "heartbeat" in embed["title"].lower()
    assert "healthy" in embed["title"].lower()
    assert embed["color"] == 3066993  # Discord green
    field_by_name = {f["name"]: f["value"] for f in embed["fields"]}
    assert field_by_name["Cluster"] == "lolday-unit-test"
    assert "42s" in field_by_name["Watchdog age"]
    assert f"{check_mod.MAX_AGE_SECONDS}s" in field_by_name["Watchdog age"]
    assert field_by_name["Timestamp"] == "2026-05-16T22:00:00+00:00"


def test_check_success_with_heartbeat_url_posts_heartbeat(check_mod):
    """Happy path: Watchdog fresh + heartbeat URL set → exactly one POST
    to the heartbeat URL with the green embed payload."""
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)
    seen_requests: list[dict] = []

    def opener(req, timeout):
        # Snapshot URL + body for assertion. The script calls _discord_post
        # once for success — verify the call shape, not the network.
        seen_requests.append({"url": req.full_url, "body": req.data})
        return _SuccessResp()

    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(fresh)]):
        reason = check_mod.check(
            now=now,
            heartbeat_url="http://heartbeat.test/webhook/abc",
            heartbeat_opener=opener,
            heartbeat_sleep=lambda *_: None,
        )

    assert reason is None
    assert len(seen_requests) == 1
    posted = seen_requests[0]
    assert posted["url"] == "http://heartbeat.test/webhook/abc"
    body = (
        posted["body"].decode() if isinstance(posted["body"], bytes) else posted["body"]
    )
    assert "heartbeat" in body.lower()
    # No @here on the heartbeat — that would re-notify a healthy state.
    assert "@here" not in body


def test_check_success_without_heartbeat_url_does_not_post(check_mod):
    """Backwards compat: existing deploys without DISCORD_HEARTBEAT_URL
    must continue to be silent on success."""
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)
    seen_requests: list[dict] = []

    def opener(req, timeout):
        seen_requests.append({"url": req.full_url})
        return _SuccessResp()

    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(fresh)]):
        reason = check_mod.check(now=now, heartbeat_opener=opener)

    assert reason is None
    assert seen_requests == [], "no heartbeat URL → no POST"


def test_check_success_heartbeat_post_failure_is_swallowed(check_mod):
    """A flapping heartbeat sink must NOT cause the CronJob to crash —
    that would page Captain Hook (KubeJobFailed) on top of Spidey
    Heartbeat silence, doubling the operator's noise."""
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)

    def opener(req, timeout):
        raise urllib.error.URLError("heartbeat sink down")

    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(fresh)]):
        # Should NOT raise — the failure must be swallowed.
        reason = check_mod.check(
            now=now,
            heartbeat_url="http://heartbeat.test/webhook/abc",
            heartbeat_opener=opener,
            heartbeat_sleep=lambda *_: None,
        )

    assert reason is None  # Watchdog itself is healthy


def test_check_failure_does_not_post_heartbeat(check_mod):
    """If Watchdog is stale or missing, the heartbeat path must be
    skipped — we shouldn't tell Spidey Heartbeat "all healthy" while
    Captain Hook is being paged."""
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=check_mod.MAX_AGE_SECONDS + 60)
    seen_requests: list[dict] = []

    def opener(req, timeout):
        seen_requests.append({"url": req.full_url})
        return _SuccessResp()

    with mock.patch.object(check_mod, "fetch_alerts", return_value=[_watchdog(stale)]):
        reason = check_mod.check(
            now=now,
            heartbeat_url="http://heartbeat.test/webhook/abc",
            heartbeat_opener=opener,
            heartbeat_sleep=lambda *_: None,
        )

    assert reason is not None
    assert "stopped sending" in reason
    assert seen_requests == [], "failure path must not also send heartbeat"
