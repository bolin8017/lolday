"""GET /api/v1/jobs/{id}/events — paged historical retrieval."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detector, DetectorVersion, Job, JobEvent, User

# user_client fixture sets X-Test-User-Email: user1@example.dev
_AUTHED_EMAIL = "user1@example.dev"


async def _seed_job_for_owner(session: AsyncSession, auth_email: str) -> Job:
    """Create a Job owned by user with email == auth_email.

    user_client fixture pre-seeds user1@example.dev; we look it up or create
    it so the cf_access_user override resolves and the owner check passes.
    """
    from sqlalchemy import select as sa_select

    existing = (
        await session.execute(sa_select(User).where(User.email == auth_email))
    ).scalar_one_or_none()
    if existing is not None:
        user = existing
    else:
        user = User(
            id=uuid.uuid4(),
            email=auth_email,
            hashed_password="x",
            is_active=True,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

    det = Detector(
        name=f"events-get-{uuid.uuid4().hex[:8]}",
        display_name="events-get",
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
        status="running",
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    session.add(job)
    await session.commit()
    return job


async def _seed_events(session: AsyncSession, job_id: uuid.UUID, items: list[dict]) -> None:
    for e in items:
        row = JobEvent(
            job_id=job_id,
            ts=datetime.fromisoformat(e["ts"].replace("Z", "+00:00")),
            kind=e["kind"],
            payload={k: v for k, v in e.items() if k not in ("ts", "kind")},
        )
        session.add(row)
    await session.commit()


@pytest.mark.asyncio
async def test_get_events_empty(db_session: AsyncSession, user_client: AsyncClient) -> None:
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    resp = await user_client.get(f"/api/v1/jobs/{job.id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["events"] == []
    assert data["next_since"] is None


@pytest.mark.asyncio
async def test_get_events_ordered_by_ts(db_session: AsyncSession, user_client: AsyncClient) -> None:
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    await _seed_events(db_session, job.id, [
        {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        {"ts": "2026-04-24T00:00:05Z", "kind": "metric", "name": "loss", "value": 0.3},
        {"ts": "2026-04-24T00:00:10Z", "kind": "stage_end", "stage": "train", "status": "success"},
    ])
    resp = await user_client.get(f"/api/v1/jobs/{job.id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert [e["kind"] for e in data["events"]] == ["stage_begin", "metric", "stage_end"]


@pytest.mark.asyncio
async def test_get_events_since_cursor(db_session: AsyncSession, user_client: AsyncClient) -> None:
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    await _seed_events(db_session, job.id, [
        {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        {"ts": "2026-04-24T00:00:10Z", "kind": "stage_end", "stage": "train", "status": "success"},
    ])
    resp = await user_client.get(
        f"/api/v1/jobs/{job.id}/events",
        params={"since": "2026-04-24T00:00:05Z"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 1
    assert data["events"][0]["kind"] == "stage_end"


@pytest.mark.asyncio
async def test_get_events_rejects_other_owner(
    db_session: AsyncSession, user_client: AsyncClient, second_user_client: AsyncClient
) -> None:
    """A job owned by user1 must not be readable by user2."""
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    resp = await second_user_client.get(f"/api/v1/jobs/{job.id}/events")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_paginate_with_tied_timestamps_does_not_skip(
    db_session: AsyncSession, user_client: AsyncClient,
) -> None:
    """Three events at the SAME ``ts`` — limit=2 must surface events [1,2]
    then event [3] across two pages, never dropping event [3] nor duplicating
    one. The naive ``ts > since`` filter skips events colliding on the
    boundary; the fix uses a ``(ts, id)`` composite cursor."""
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    shared_ts = datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc)
    for kind in ("a", "b", "c"):
        db_session.add(JobEvent(
            job_id=job.id, ts=shared_ts, kind=kind, payload={},
        ))
    await db_session.commit()

    # First page, limit=2
    resp = await user_client.get(
        f"/api/v1/jobs/{job.id}/events", params={"limit": 2}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 2
    assert data["next_since"] is not None
    assert data["next_id"] is not None
    seen_first = {e["kind"] for e in data["events"]}

    # Second page using composite cursor
    resp = await user_client.get(
        f"/api/v1/jobs/{job.id}/events",
        params={
            "limit": 2,
            "since": data["next_since"],
            "since_id": data["next_id"],
        },
    )
    assert resp.status_code == 200
    data2 = resp.json()
    # Exactly one more event — the third of the three shared-ts events
    assert len(data2["events"]) == 1
    seen_second = {e["kind"] for e in data2["events"]}
    # No overlap, full coverage of a/b/c
    assert seen_first.isdisjoint(seen_second)
    assert seen_first | seen_second == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_paginate_without_cursor_is_backward_compatible(
    db_session: AsyncSession, user_client: AsyncClient,
) -> None:
    """Clients that only pass ``since`` (no ``since_id``) still get the
    old ``ts > since`` semantics, skipping events equal to the cursor."""
    job = await _seed_job_for_owner(db_session, _AUTHED_EMAIL)
    await _seed_events(db_session, job.id, [
        {"ts": "2026-04-24T00:00:00Z", "kind": "stage_begin", "stage": "train"},
        {"ts": "2026-04-24T00:00:10Z", "kind": "stage_end", "stage": "train", "status": "success"},
    ])
    resp = await user_client.get(
        f"/api/v1/jobs/{job.id}/events",
        params={"since": "2026-04-24T00:00:00Z"},
    )
    data = resp.json()
    assert [e["kind"] for e in data["events"]] == ["stage_end"]
