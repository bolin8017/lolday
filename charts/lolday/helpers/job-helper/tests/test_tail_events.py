"""tail_events: tails an NDJSON file and POSTs each event to the backend."""

from __future__ import annotations

import json
from pathlib import Path

import respx
from httpx import Response

from job_helper.tail_events import tail_and_post


@respx.mock
def test_tail_existing_events(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    out.write_text(
        json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}) + "\n"
        + json.dumps({"ts": "2026-04-24T00:01:00Z", "kind": "stage_end", "stage": "train", "status": "success"}) + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(return_value=Response(202))

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
    out.write_text(json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "metric", "name": "loss", "value": 0.1}) + "\n")

    route = respx.post("http://backend/internal/jobs/x/events").mock(return_value=Response(202))

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
    out.write_text(json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "metric", "name": "loss", "value": 0.1}) + "\n")

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
        + json.dumps({"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}) + "\n"
    )

    route = respx.post("http://backend/internal/jobs/x/events").mock(return_value=Response(202))

    tail_and_post(
        events_path=out,
        endpoint_url="http://backend/internal/jobs/x/events",
        job_token="t",
        stop_on_eof=True,
    )

    assert route.call_count == 1
