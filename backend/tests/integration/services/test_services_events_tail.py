"""events_tail: persist an event into job_events + broadcast to subscribers."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.models import Detector, DetectorVersion, Job, JobEvent, User
from app.services.events_tail import EventBroker, _parse_ts, persist_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_job(session: AsyncSession) -> Job:
    """Minimal Job + required parents. Adapt field names to whatever the models require."""
    _uid = uuid.uuid4()
    user = User(
        id=_uid,
        email=f"events-{_uid.hex[:8]}@example.com",
        handle=f"events-{_uid.hex[:8]}",
    )
    session.add(user)
    await session.flush()  # user must be persisted before Detector FK can reference it
    det = Detector(
        name=f"events-det-{uuid.uuid4().hex[:8]}",
        display_name="events-det",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    session.add(det)
    await session.flush()

    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="deadbeef",
        harbor_image="h/x:v1",
        image_digest="sha256:abc",
    )
    session.add(dv)
    await session.flush()

    job = Job(
        type="train",
        status="pending",
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    session.add(job)
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_persist_event_inserts_row(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    event = {
        "ts": "2026-04-24T00:00:00Z",
        "kind": "metric",
        "name": "train_loss",
        "value": 0.34,
        "step": 1,
    }
    await persist_event(db_session, job_id=job.id, event=event)
    rows = list(
        (
            await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].kind == "metric"
    assert rows[0].payload["name"] == "train_loss"
    assert rows[0].payload["value"] == 0.34


@pytest.mark.asyncio
async def test_persist_event_handles_missing_ts(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    event = {"kind": "stage_begin", "stage": "train"}
    await persist_event(db_session, job_id=job.id, event=event)
    rows = list(
        (
            await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
        ).all()
    )
    assert rows[0].kind == "stage_begin"
    assert rows[0].ts is not None


@pytest.mark.asyncio
async def test_broadcast_delivers_to_subscriber() -> None:
    broker = EventBroker()
    jid = uuid.uuid4()
    queue: asyncio.Queue = broker.subscribe(jid)
    event = {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"}
    await broker.publish(jid, event)
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received == event


@pytest.mark.asyncio
async def test_unsubscribe_drops_queue() -> None:
    broker = EventBroker()
    jid = uuid.uuid4()
    q = broker.subscribe(jid)
    broker.unsubscribe(jid, q)
    await broker.publish(jid, {"kind": "test"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_queue_full_drops_oldest() -> None:
    broker = EventBroker()
    jid = uuid.uuid4()
    q = broker.subscribe(jid)
    for i in range(1001):
        await broker.publish(jid, {"kind": "evt", "i": i})
    assert q.qsize() <= 1000


@pytest.mark.asyncio
async def test_publish_reaches_all_concurrent_subscribers() -> None:
    """Two WebSocket clients watching the same job — both should see every event."""
    broker = EventBroker()
    jid = uuid.uuid4()
    q1 = broker.subscribe(jid)
    q2 = broker.subscribe(jid)
    await broker.publish(jid, {"kind": "metric", "name": "loss", "value": 0.1})
    e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert e1["name"] == "loss"
    assert e2["name"] == "loss"


def test_parse_ts_invalid_string_returns_none() -> None:
    """A malformed timestamp string must surface as None so persist_event
    falls back to `datetime.now(UTC)` — covers the `except ValueError`
    arm of `_parse_ts`. The maldet-side serialiser is the source of
    truth, but historical replay can hand the broker arbitrary strings.
    """
    assert _parse_ts("not-an-isoformat") is None


def test_unsubscribe_noop_when_subscriber_already_gone() -> None:
    """Unsubscribing for a job_id with no recorded subscribers (or a queue
    that was already removed) must not raise — covers the `queues=None`
    falsy-branch in `EventBroker.unsubscribe`.

    Trigger via a fresh job_id that was never subscribed to, plus a second
    unsubscribe of an already-removed queue from a different job_id.
    """
    broker = EventBroker()
    # Never-subscribed job_id → `self._subscribers.get(jid)` returns None.
    broker.unsubscribe(uuid.uuid4(), asyncio.Queue())

    # Subscribed-then-unsubscribed → second call sees empty `queues` list
    # but the `queues and q in queues` short-circuit kicks in first.
    jid = uuid.uuid4()
    q = broker.subscribe(jid)
    broker.unsubscribe(jid, q)
    broker.unsubscribe(jid, q)  # idempotent, must not raise


def test_unsubscribe_leaves_other_subscribers_alive() -> None:
    """Two subscribers on the same job_id, drop one — the remaining queue
    must still receive events. Covers the `queues is not None and not queues`
    branch (the partial-empty arm of the pop guard)."""
    broker = EventBroker()
    jid = uuid.uuid4()
    q1 = broker.subscribe(jid)
    q2 = broker.subscribe(jid)
    broker.unsubscribe(jid, q1)
    # The job_id entry must NOT be popped (q2 still subscribed).
    assert jid in broker._subscribers
    assert q2 in broker._subscribers[jid]
