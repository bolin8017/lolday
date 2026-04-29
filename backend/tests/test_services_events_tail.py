"""events_tail: persist an event into job_events + broadcast to subscribers."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detector, DetectorVersion, Job, JobEvent, User
from app.services.events_tail import EventBroker, persist_event


async def _seed_job(session: AsyncSession) -> Job:
    """Minimal Job + required parents. Adapt field names to whatever the models require."""
    user = User(
        id=uuid.uuid4(),
        email=f"events-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
    )
    det = Detector(
        name=f"events-det-{uuid.uuid4().hex[:8]}",
        display_name="events-det",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    session.add_all([user, det])
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
    rows = list((await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))).all())
    assert len(rows) == 1
    assert rows[0].kind == "metric"
    assert rows[0].payload["name"] == "train_loss"
    assert rows[0].payload["value"] == 0.34


@pytest.mark.asyncio
async def test_persist_event_handles_missing_ts(db_session: AsyncSession) -> None:
    job = await _seed_job(db_session)
    event = {"kind": "stage_begin", "stage": "train"}
    await persist_event(db_session, job_id=job.id, event=event)
    rows = list((await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))).all())
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
