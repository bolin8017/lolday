"""Tail an NDJSON event file and POST each line to the backend's internal events endpoint.

Volcano Job sidecar container. The detector writes
``/mnt/output/events.jsonl`` line-by-line with ``fsync`` after each event.
This tailer follows the file and forwards each event to the backend for
persistence + WebSocket broadcast.

After the detector exits (EOF on the file), the tailer waits
``GRACE_SECONDS`` to drain any late fsyncs / OS buffers, then exits —
otherwise Volcano could reap the sidecar container before the last few
events (including ``stage_end``) flush, causing the reconciler to fall
back to Volcano-phase polling.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

GRACE_SECONDS = 30


def tail_and_post(
    *,
    events_path: Path,
    endpoint_url: str,
    job_token: str,
    stop_on_eof: bool = False,
    grace_seconds: int = GRACE_SECONDS,
    poll_interval_s: float = 0.5,
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.touch(exist_ok=True)

    last_activity = time.monotonic()
    with (
        httpx.Client(timeout=10.0) as client,
        events_path.open("r", encoding="utf-8") as f,
    ):
        while True:
            line = f.readline()
            if line:
                last_activity = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _post_with_retry(client, endpoint_url, job_token, event)
                continue

            if stop_on_eof:
                return

            if time.monotonic() - last_activity > grace_seconds:
                return
            time.sleep(poll_interval_s)


def _post_with_retry(
    client: httpx.Client, url: str, token: str, event: dict[str, Any]
) -> None:
    delay = 0.5
    for attempt in range(6):
        try:
            resp = client.post(
                url,
                json=event,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            if attempt == 5:
                sys.stderr.write(
                    f"tail_events: giving up after 6 attempts, event lost: {exc}\n"
                )
                return
            time.sleep(delay)
            delay = min(delay * 2, 10.0)
            continue

        if 200 <= resp.status_code < 300:
            return
        if 400 <= resp.status_code < 500:
            # 401/403/404/409/422/429 are permanent — retrying just wastes cycles
            # and keeps the sidecar alive after Volcano has moved on.
            sys.stderr.write(
                f"tail_events: permanent {resp.status_code} from backend, event lost; "
                f"body={resp.text[:200]!r}\n"
            )
            return
        # 5xx → retry with backoff
        if attempt == 5:
            sys.stderr.write(
                f"tail_events: giving up after 6 attempts at 5xx, event lost; "
                f"last={resp.status_code}\n"
            )
            return
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


def _main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write(
            "usage: python -m job_helper.tail_events <path/to/events.jsonl>\n"
        )
        sys.exit(2)
    events_path = Path(args[0])
    endpoint_url = os.environ["INTERNAL_EVENTS_URL"]
    job_token = os.environ["JOB_TOKEN"]
    tail_and_post(
        events_path=events_path, endpoint_url=endpoint_url, job_token=job_token
    )


if __name__ == "__main__":
    _main()
