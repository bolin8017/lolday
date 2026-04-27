"""On stage_end, reconciler aggregates last-per-name metric events into summary_metrics."""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobEvent
from app.models.job import JobStatus, JobType, ResourceProfile
from app.reconciler import _project_summary_metrics


async def _make_terminal_job(session: AsyncSession) -> Job:
    job = Job(
        id=_uuid.uuid4(),
        type=JobType.TRAIN,
        status=JobStatus.SUCCEEDED,
        owner_id=_uuid.uuid4(),
        detector_version_id=_uuid.uuid4(),
        resource_profile=ResourceProfile.STANDARD,
        resolved_config={},
        idempotency_key="test-" + _uuid.uuid4().hex,
        submitted_at=_dt.datetime.now(_dt.timezone.utc),
    )
    session.add(job)
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_projection_takes_last_metric_per_name(db_session: AsyncSession) -> None:
    job = await _make_terminal_job(db_session)
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base,
        kind="metric", payload={"name": "train_loss", "value": 1.0, "step": 0},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base + _dt.timedelta(seconds=1),
        kind="metric", payload={"name": "train_loss", "value": 0.1, "step": 5},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base + _dt.timedelta(seconds=2),
        kind="confusion_matrix", payload={"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]},
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)

    assert job.summary_metrics == {
        "metrics": {"train_loss": 0.1},
        "confusion_matrix": {"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]},
    }


@pytest.mark.asyncio
async def test_projection_empty_when_no_metric_events(db_session: AsyncSession) -> None:
    job = await _make_terminal_job(db_session)
    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)
    assert job.summary_metrics == {"metrics": {}, "confusion_matrix": None}


@pytest.mark.asyncio
async def test_projection_idempotent(db_session: AsyncSession) -> None:
    job = await _make_terminal_job(db_session)
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base,
        kind="metric", payload={"name": "acc", "value": 0.99},
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)
    first = dict(job.summary_metrics)

    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)
    assert job.summary_metrics == first


@pytest.mark.asyncio
async def test_projection_takes_latest_confusion_matrix(db_session: AsyncSession) -> None:
    """If multiple confusion_matrix events appear (rerun), keep the latest by ts."""
    job = await _make_terminal_job(db_session)
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base,
        kind="confusion_matrix", payload={"labels": ["a", "b"], "matrix": [[1, 1], [1, 1]]},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base + _dt.timedelta(seconds=1),
        kind="confusion_matrix", payload={"labels": ["x", "y"], "matrix": [[2, 0], [0, 2]]},
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)
    assert job.summary_metrics["confusion_matrix"] == {"labels": ["x", "y"], "matrix": [[2, 0], [0, 2]]}


@pytest.mark.asyncio
async def test_projection_skips_malformed_metric_payload(db_session: AsyncSession) -> None:
    """Defensive: a metric event with non-numeric value or missing name is skipped, not crashed."""
    job = await _make_terminal_job(db_session)
    base = _dt.datetime.now(_dt.timezone.utc)
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base,
        kind="metric", payload={"name": "good", "value": 0.5},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base + _dt.timedelta(seconds=1),
        kind="metric", payload={"name": "bad", "value": "not-a-number"},
    ))
    db_session.add(JobEvent(
        id=_uuid.uuid4(), job_id=job.id, ts=base + _dt.timedelta(seconds=2),
        kind="metric", payload={"value": 1.0},  # missing name
    ))
    await db_session.commit()

    await _project_summary_metrics(db_session, job.id)
    await db_session.refresh(job)
    assert job.summary_metrics["metrics"] == {"good": 0.5}
