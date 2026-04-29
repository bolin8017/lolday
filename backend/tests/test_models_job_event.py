"""JobEvent ORM insert/select round-trip."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detector, DetectorVersion, Job, JobEvent, User


@pytest.mark.asyncio
async def test_insert_and_query(db_session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        email="t@example.com",
        hashed_password="x",
    )
    det = Detector(
        name="d1-task3",
        display_name="d1",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    db_session.add_all([user, det])
    await db_session.flush()

    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="harbor.harbor.svc:80/detectors/d1:v1",
        image_digest="sha256:" + "a" * 64,
    )
    db_session.add(dv)
    await db_session.flush()

    job = Job(
        type="train",
        status="pending",
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    db_session.add(job)
    await db_session.commit()

    ev = JobEvent(
        job_id=job.id,
        ts=datetime(2026, 4, 24, tzinfo=timezone.utc),
        kind="stage_begin",
        payload={"stage": "train"},
    )
    db_session.add(ev)
    await db_session.commit()

    result = await db_session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.ts)
    )
    rows = list(result)
    assert len(rows) == 1
    assert rows[0].kind == "stage_begin"
    assert rows[0].payload == {"stage": "train"}
