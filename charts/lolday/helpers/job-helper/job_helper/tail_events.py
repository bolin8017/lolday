"""Tail an NDJSON event file and POST each line to the backend's internal events endpoint.

Designed as a Volcano Job sidecar container. When the detector's `events.jsonl`
file is written line-by-line (fsync per line), this tailer reads each appended
line and forwards it to the backend for persistence + WebSocket broadcast.

When the detector exits, the sidecar sees EOF. It continues to read for a grace
period to drain trailing events, then exits.
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
    with httpx.Client(timeout=10.0) as client:
        with events_path.open("r", encoding="utf-8") as f:
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


def _post_with_retry(client: httpx.Client, url: str, token: str, event: dict[str, Any]) -> None:
    delay = 0.5
    for _attempt in range(6):
        try:
            resp = client.post(
                url,
                json=event,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 10.0)


def _main() -> None:
    args = sys.argv[1:]
    if len(args) != 1:
        sys.stderr.write("usage: python -m job_helper.tail_events <path/to/events.jsonl>\n")
        sys.exit(2)
    events_path = Path(args[0])
    endpoint_url = os.environ["INTERNAL_EVENTS_URL"]
    job_token = os.environ["JOB_TOKEN"]
    tail_and_post(events_path=events_path, endpoint_url=endpoint_url, job_token=job_token)


if __name__ == "__main__":
    _main()
