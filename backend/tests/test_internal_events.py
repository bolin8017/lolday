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
    _uid = uuid.uuid4()
    user = User(
        id=_uid,
        email=f"events-int-{_uid.hex[:8]}@example.com",
        handle=f"events-int-{_uid.hex[:8]}",
    )
    session.add(user)
    await session.flush()  # user must be persisted before Detector FK can reference it
    det = Detector(
        name=f"events-int-{uuid.uuid4().hex[:8]}",
        display_name="events-int",
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
async def test_post_event_persists_and_accepts(
    db_session, internal_client: AsyncClient
) -> None:
    job, raw_token = await _seed_job_with_token(db_session)
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "stage_begin", "stage": "train"},
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
    db_session, internal_client: AsyncClient
) -> None:
    job, _ = await _seed_job_with_token(db_session)
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "stage_begin", "stage": "train"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_post_event_rejects_wrong_job_id(
    db_session, internal_client: AsyncClient
) -> None:
    _job, raw_token = await _seed_job_with_token(db_session)
    other_id = uuid.uuid4()
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{other_id}/events",
        json={"kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    # Token is scoped to the original job; cross-job POST returns 404 (job not found)
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_post_event_publishes_to_broker(
    db_session, internal_client: AsyncClient
) -> None:
    import asyncio

    from app.services.events_tail import event_broker

    job, raw_token = await _seed_job_with_token(db_session)
    q = event_broker.subscribe(job.id)
    try:
        await internal_client.post(
            f"/api/v1/internal/jobs/{job.id}/events",
            json={
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
async def test_post_event_rejects_terminal_job(
    db_session, internal_client: AsyncClient
) -> None:
    """A sidecar race with the reconciler can POST an event AFTER the job is
    already flipped to SUCCEEDED/FAILED. The H-20 dep gate in require_job_token
    now rejects terminal jobs with 404 before the router's 409 check fires."""
    job, raw_token = await _seed_job_with_token(db_session, status=JobStatus.SUCCEEDED)
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={
            "kind": "metric",
            "name": "loss",
            "value": 0.9,
        },
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code in (404, 409)


# ---------- new tests for M-event-dict schema enforcement ----------


@pytest.mark.asyncio
async def test_post_event_rejects_unknown_kind(
    db_session, internal_client: AsyncClient
) -> None:
    """Unknown kind value must be rejected with 422."""
    job, raw_token = await _seed_job_with_token(db_session)
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "totally_unknown_kind"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_event_rejects_oversized_event(
    db_session, internal_client: AsyncClient
) -> None:
    """Whole event exceeding 64 KiB must be rejected with 413."""
    job, raw_token = await _seed_job_with_token(db_session)
    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "metric", "name": "loss", "huge_blob": "x" * 200_000},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert resp.status_code == 413


# ---------- H-20: job token invalidation on cancel / terminal status ----------


@pytest.mark.asyncio
async def test_token_rejected_after_cancel(
    db_session, user_client: AsyncClient, internal_client: AsyncClient
) -> None:
    """H-20 cancel path: token must be rejected (404) after the job is cancelled.

    Uses two clients in tandem after the M-internal-split: ``user_client``
    drives the user-facing ``/api/v1/jobs/{id}/cancel`` route (port 8000 in
    prod), and ``internal_client`` drives ``/api/v1/internal/*`` (port 8001).
    """
    from app.models import Detector, DetectorVersion, User
    from app.services.job_tokens import generate_token, hash_token

    # Seed a job owned by the user behind user_client (user1@example.dev).
    from sqlalchemy import select

    owner = (
        await db_session.scalars(select(User).where(User.email == "user1@example.dev"))
    ).first()
    det = Detector(
        name=f"h20-cancel-{uuid.uuid4().hex[:8]}",
        display_name="h20-cancel",
        owner_id=owner.id,
        git_url="https://example.com/r.git",
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="deadbeef",
        harbor_image="h/x:v1",
        image_digest="sha256:abc",
    )
    db_session.add(dv)
    await db_session.flush()
    raw_token = generate_token()
    job = Job(
        type="train",
        status=JobStatus.RUNNING,
        owner_id=owner.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        token_hash=hash_token(raw_token),
    )
    db_session.add(job)
    await db_session.commit()

    # Confirm token works before cancel (internal sub-app).
    pre_resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert pre_resp.status_code == 202

    # Cancel the job via the user session client (public API).
    cancel_resp = await user_client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert cancel_resp.status_code == 200

    # Token must now be rejected on the internal sub-app.
    post_resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert post_resp.status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_token_rejected_when_job_already_terminal_in_db(
    db_session, internal_client: AsyncClient
) -> None:
    """H-20 defense-in-depth: require_job_token must reject terminal jobs even
    when token_hash has NOT been nulled yet (e.g. a race before the reconciler
    runs the cleanup).
    """
    # Seed a job with a valid token but with SUCCEEDED status (terminal),
    # deliberately skipping the null-out to exercise the dep-level gate.
    job, raw_token = await _seed_job_with_token(db_session, status=JobStatus.SUCCEEDED)

    resp = await internal_client.post(
        f"/api/v1/internal/jobs/{job.id}/events",
        json={"kind": "stage_begin", "stage": "train"},
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    # The dep must reject the request regardless of token validity.
    assert resp.status_code in (401, 403, 404)
