"""tail_events: tails an NDJSON file and POSTs each event to the backend."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from httpx import Response
from job_helper.tail_events import _post_with_retry, tail_and_post


@respx.mock
def test_tail_existing_events(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(
        json.dumps(
            {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-04-24T00:01:00Z",
                "kind": "stage_end",
                "stage": "train",
                "status": "success",
            }
        )
        + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(
        return_value=Response(202)
    )

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,
    )

    assert route.call_count == 2
    bodies = [json.loads(req.request.content) for req in route.calls]
    assert bodies[0]["kind"] == "stage_begin"
    assert bodies[1]["kind"] == "stage_end"


@respx.mock
def test_auth_header_carries_token(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(
        json.dumps(
            {
                "ts": "2026-04-24T00:00:00Z",
                "kind": "metric",
                "name": "loss",
                "value": 0.1,
            }
        )
        + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(
        return_value=Response(202)
    )

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="secret-token",
        stop_on_eof=True,
    )

    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer secret-token"


@respx.mock
def test_retry_on_transient_failure(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(
        json.dumps(
            {
                "ts": "2026-04-24T00:00:00Z",
                "kind": "metric",
                "name": "loss",
                "value": 0.1,
            }
        )
        + "\n"
    )

    respx.post("http://backend/internal/jobs/x/events").mock(
        side_effect=[Response(503), Response(503), Response(202)]
    )

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,
    )
    assert len(respx.calls) >= 3


@respx.mock
def test_malformed_lines_skipped(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(
        "not json\n"
        + json.dumps(
            {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}
        )
        + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(
        return_value=Response(202)
    )

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,
    )

    assert route.call_count == 1


@respx.mock
def test_429_is_permanent_not_retried() -> None:
    """429 is a 4xx — the backend rejected the event; retrying is a busy-loop."""
    route = respx.post("http://backend/internal/jobs/x/events").mock(
        return_value=Response(429, text="rate limited")
    )
    with httpx.Client() as c:
        _post_with_retry(c, "http://backend/internal/jobs/x/events", "t", {"k": "v"})
    assert route.call_count == 1


@respx.mock
def test_4xx_is_permanent_single_attempt() -> None:
    """Every 4xx status is terminal: validation errors, auth errors, terminal
    409 (job in terminal state) — all pointless to retry."""
    for code in (400, 401, 403, 404, 409, 422):
        route = respx.post(f"http://backend/4xx-{code}").mock(
            return_value=Response(code, text=f"status={code}")
        )
        with httpx.Client() as c:
            _post_with_retry(c, f"http://backend/4xx-{code}", "t", {"k": "v"})
        assert route.call_count == 1, f"status={code} should not retry"


@respx.mock
def test_retry_exhaustion_logs_to_stderr(monkeypatch, capsys) -> None:
    """Six 5xx in a row → log a single line to stderr with the last status."""
    monkeypatch.setattr("job_helper.tail_events.time.sleep", lambda _s: None)
    respx.post("http://backend/internal/jobs/x/events").mock(
        return_value=Response(503, text="down")
    )
    with httpx.Client() as c:
        _post_with_retry(c, "http://backend/internal/jobs/x/events", "t", {"k": "v"})
    captured = capsys.readouterr()
    assert "giving up" in captured.err
    assert "503" in captured.err


@respx.mock
def test_network_error_exhaustion_logs_to_stderr(monkeypatch, capsys) -> None:
    """Six network errors in a row → log a single line to stderr with the exc."""
    monkeypatch.setattr("job_helper.tail_events.time.sleep", lambda _s: None)
    respx.post("http://backend/internal/jobs/x/events").mock(
        side_effect=httpx.ConnectError("boom")
    )
    with httpx.Client() as c:
        _post_with_retry(c, "http://backend/internal/jobs/x/events", "t", {"k": "v"})
    captured = capsys.readouterr()
    assert "giving up" in captured.err
