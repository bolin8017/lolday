"""Phase 11b event stream: persistence + in-process WebSocket broadcast."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobEvent


def _parse_ts(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def persist_event(
    session: AsyncSession, *, job_id: uuid.UUID, event: dict[str, Any]
) -> JobEvent:
    ts = _parse_ts(event.get("ts")) or datetime.now(timezone.utc)
    kind = event.get("kind") or "unknown"
    payload = {k: v for k, v in event.items() if k not in ("ts", "kind")}
    row = JobEvent(job_id=job_id, ts=ts, kind=kind, payload=payload)
    session.add(row)
    await session.commit()
    return row


class EventBroker:
    """In-process fan-out of events to WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, job_id: uuid.UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[job_id].append(q)
        return q

    def unsubscribe(self, job_id: uuid.UUID, q: asyncio.Queue) -> None:
        queues = self._subscribers.get(job_id)
        if queues and q in queues:
            queues.remove(q)
        if queues is not None and not queues:
            self._subscribers.pop(job_id, None)

    async def publish(self, job_id: uuid.UUID, event: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(job_id, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(event)


event_broker = EventBroker()
