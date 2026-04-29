"""POST /internal/jobs/{id}/events — sidecar authenticated via job token."""

from __future__ import annotations

import uuid

import pytest
from app.models import Detector, DetectorVersion, Job, User
from app.models.job import JobStatus
from app.services.job_tokens import generate_token, hash_token
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_job_with_token(
    session: AsyncSession, *, status: JobStatus = JobStatus.RUNNING
) -> tuple[Job, str]:
    """Create a Job + issue a token for its sidecar."""
    user = User(
        id=uuid.uuid4(),
        email=f"events-int-{uuid.uuid4().hex[:8]}@example.com",
    )
    det = Detector(
        name=f"events-int-{uuid.uuid4().hex[:8]}",
        display_name="events-int",
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
    raw_token = generate_token()
    job = Job(
        type="train",
        status=status,
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        token_hash=hash_token(raw_token),
    )
    session.add(job)
    await session.commit()
    return job, raw_token


@pytest.mark.asyncio
async def test_post_event_persists_and_accepts(db_session, client: AsyncClient) -> None:
    job, raw_token = await _seed_job_with_token(db_session)
    resp = await client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 202
    # Verify row persisted
    from app.models import JobEvent
    from sqlalchemy import select

    rows = (
        await db_session.scalars(select(JobEvent).where(JobEvent.job_id == job.id))
    ).all()
    assert len(list(rows)) == 1


@pytest.mark.asyncio
async def test_post_event_rejects_invalid_token(
    db_session, client: AsyncClient
) -> None:
    job, _ = await _seed_job_with_token(db_session)
    resp = await client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_post_event_rejects_wrong_job_id(db_session, client: AsyncClient) -> None:
    _job, raw_token = await _seed_job_with_token(db_session)
    other_id = uuid.uuid4()
    resp = await client.post(
        f"/api/v1/internal/jobs/{other_id}/events",
        json={"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    # Token is scoped to the original job; cross-job POST returns 404 (job not found)
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_post_event_publishes_to_broker(db_session, client: AsyncClient) -> None:
    import asyncio

    from app.services.events_tail import event_broker

    job, raw_token = await _seed_job_with_token(db_session)
    q = event_broker.subscribe(job.id)
    try:
        await client.post(
            f"/api/v1/internal/jobs/{job.id}/events",
            json={
                "ts": "2026-04-24T00:00:00Z",
                "kind": "metric",
                "name": "loss",
                "value": 0.1,
            },
            headers={"Authorization": f"Bearer {raw_token}"},
        )
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event["kind"] == "metric"
        assert event["name"] == "loss"
    finally:
        event_broker.unsubscribe(job.id, q)


@pytest.mark.asyncio
async def test_post_event_rejects_terminal_job(db_session, client: AsyncClient) -> None:
    """A sidecar race with the reconciler can POST an event AFTER the job is
    already flipped to SUCCEEDED/FAILED. We return 409 to bound the amount of
    state written to the terminal row."""
    job, raw_token = await _seed_job_with_token(db_session, status=JobStatus.SUCCEEDED)
    resp = await client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={
            "ts": "2026-04-24T00:00:00Z",
            "kind": "metric",
            "name": "loss",
            "value": 0.9,
        },
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 409
