"""Verify Watchdog alert freshness in Alertmanager; Discord on failure.

Exits 0 on both happy-path and after-sending-Discord-alert. A failed
Job would fire KubeJobFailed via kube-state-metrics and double-page;
we already delivered the signal via the independent Discord channel.

This module is mounted into the deadmans-switch CronJob via helm
`.Files.Get` and invoked by `python /scripts/check.py`. It is also
imported directly in `backend/tests/test_deadmans_switch_check.py`
via `importlib.util.spec_from_file_location` so the parse / retry /
failure paths have real unit coverage without needing a running k8s.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

AM_URL = "http://kps-alertmanager.monitoring:9093/api/v2/alerts"
MAX_AGE_SECONDS = 600  # 10 min: covers 5-min cron jitter plus one missed eval
TIMEOUT_SECONDS = 15
USER_AGENT = "lolday-deadmans-switch/1.0 (+https://github.com/louiskyee/lolday)"
# Max attempts for the Discord POST. Only retries on transient
# 429 / 5xx / network errors; `Retry-After` is honoured when present.
DISCORD_MAX_ATTEMPTS = 3


def fetch_alerts():
    req = urllib.request.Request(
        AM_URL,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.load(resp)


def check(now=None):
    """Return None if Watchdog is fresh, else a human-readable failure reason."""
    if now is None:
        now = datetime.now(UTC)
    try:
        alerts = fetch_alerts()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"Alertmanager unreachable: {type(exc).__name__}: {exc}"
    if not isinstance(alerts, list):
        return f"Alertmanager response was not a list (got {type(alerts).__name__})"
    watchdog = next(
        (a for a in alerts if a.get("labels", {}).get("alertname") == "Watchdog"),
        None,
    )
    if watchdog is None:
        return "Watchdog alert is not present in Alertmanager — the entire Prometheus → Alertmanager chain is broken"
    # `updatedAt` refreshes every time Alertmanager receives this alert
    # from Prometheus; `startsAt` would only move when the alert first
    # fires, so it is useless as a heartbeat. If `updatedAt` ages out,
    # Prometheus has stopped evaluating-and-sending.
    updated_at_raw = watchdog.get("updatedAt", "")
    try:
        updated_at = datetime.fromisoformat(updated_at_raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return f"Watchdog.updatedAt unparseable: {updated_at_raw!r}"
    age = (now - updated_at).total_seconds()
    if age > MAX_AGE_SECONDS:
        return f"Watchdog.updatedAt is {int(age)}s old (max {MAX_AGE_SECONDS}s) — Prometheus has stopped sending"
    print(f"OK: Watchdog updated {int(age)}s ago")
    return None


def _build_payload(reason, now=None):
    if now is None:
        now = datetime.now(UTC)
    return {
        "content": "@here",
        "embeds": [
            {
                "title": "🚨 Dead Man's Switch — Prometheus/Alertmanager chain is broken",
                "color": 15158332,
                "description": reason,
                "fields": [
                    {
                        "name": "Cluster",
                        "value": os.environ.get("CLUSTER_NAME", "lolday"),
                        "inline": True,
                    },
                    {
                        "name": "Timestamp",
                        "value": now.isoformat(timespec="seconds"),
                        "inline": True,
                    },
                    {"name": "Next check", "value": "5 minutes", "inline": True},
                    {
                        "name": "Investigate",
                        "value": "`kubectl -n monitoring get pods -l app.kubernetes.io/name=alertmanager` / `prometheus` — check WAL, rule health, scrape target state",
                    },
                ],
            }
        ],
    }


def _discord_post(url, payload, *, opener=urllib.request.urlopen, sleep=time.sleep):
    """POST with limited retry on 429 / 5xx / transient errors.

    opener + sleep are injectable so unit tests don't hit real network.
    Returns on success; raises the last exception / HTTPError on exhaust.
    """
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    last_exc = None
    for attempt in range(1, DISCORD_MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with opener(req, timeout=TIMEOUT_SECONDS):
                return
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # Retry only on ratelimit + server-side; 4xx (except 429)
            # is a config bug (wrong webhook URL, forbidden, etc.).
            if exc.code != 429 and exc.code < 500:
                raise
            retry_after = _parse_retry_after(exc.headers)
            if attempt < DISCORD_MAX_ATTEMPTS:
                sleep(retry_after)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt < DISCORD_MAX_ATTEMPTS:
                sleep(2**attempt)
    # Exhausted; raise the final attempt's exception so main() can
    # log the concrete cause.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Discord POST failed with no recorded exception")  # unreachable


def _parse_retry_after(headers):
    raw = headers.get("Retry-After") if headers is not None else None
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def alert_discord(reason, *, now=None, opener=urllib.request.urlopen, sleep=time.sleep):
    # Fail-fast if the Secret isn't wired — CrashLoopBackOff is
    # observable via KubePodCrashLooping (which kube-state-metrics
    # fires on) even when Prometheus itself is healthy; a swallowed
    # KeyError here would make the switch appear to work forever.
    try:
        url = os.environ["DISCORD_URL"]
    except KeyError:
        raise RuntimeError(
            "DISCORD_URL env var missing — the deadmans-switch Secret "
            "`alertmanager-discord/webhook-url-critical` is misconfigured"
        ) from None
    _discord_post(url, _build_payload(reason, now=now), opener=opener, sleep=sleep)


def main():
    reason = check()
    if reason is None:
        return 0
    print(f"ALERT: {reason}", file=sys.stderr)
    try:
        alert_discord(reason)
    except urllib.error.HTTPError as exc:
        print(f"Discord POST failed: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"Discord POST failed (network): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as exc:
        # Config error (missing env var). Crash-fail so the CronJob's
        # KubeJobFailed + KubePodCrashLooping alerts make it visible.
        print(f"Discord POST config error: {exc}", file=sys.stderr)
        raise
    print(f"Discord alerted: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
