"""Phase 13b B1: prediction summary projection.

After a predict job terminates ``succeeded``, the reconciler reads
``predictions.csv`` from the MLflow run's artifact store, computes a
class-distribution summary, and caches it into
``Job.summary_metrics["prediction_summary"]``. The frontend's
PredictionSummaryCard reads from this cache; failures must NEVER raise
out of the projector — projection failure is observability tech debt,
not a job-state issue.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job
from app.models.job import JobStatus, JobType, ResourceProfile


async def _make_predict_job(
    session: AsyncSession,
    mlflow_run_id: str | None = "run-123",
    started_at: _dt.datetime | None = None,
    finished_at: _dt.datetime | None = None,
) -> Job:
    job = Job(
        id=_uuid.uuid4(),
        type=JobType.PREDICT,
        status=JobStatus.SUCCEEDED,
        owner_id=_uuid.uuid4(),
        detector_version_id=_uuid.uuid4(),
        resource_profile=ResourceProfile.STANDARD,
        resolved_config={},
        idempotency_key="test-" + _uuid.uuid4().hex,
        submitted_at=_dt.datetime.now(_dt.timezone.utc),
        mlflow_run_id=mlflow_run_id,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(job)
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_project_prediction_summary_writes_to_summary_metrics(
    db_session: AsyncSession,
) -> None:
    started = _dt.datetime.now(_dt.timezone.utc)
    finished = started + _dt.timedelta(seconds=12)
    job = await _make_predict_job(
        db_session, started_at=started, finished_at=finished
    )

    csv = "sha256,predicted_class\nA,Malware\nB,Benign\nC,Malware\nD,Malware\n"
    with patch(
        "app.reconciler._read_mlflow_artifact",
        new=AsyncMock(return_value=csv),
    ):
        from app.reconciler import _project_prediction_summary
        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    ps = job.summary_metrics["prediction_summary"]
    assert ps["total"] == 4
    assert ps["distribution"] == {"Malware": 3, "Benign": 1}
    assert ps["duration_seconds"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_project_prediction_summary_handles_missing_csv(
    db_session: AsyncSession,
) -> None:
    job = await _make_predict_job(db_session)
    with patch(
        "app.reconciler._read_mlflow_artifact",
        new=AsyncMock(side_effect=FileNotFoundError("no predictions.csv")),
    ):
        from app.reconciler import _project_prediction_summary
        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    ps = (job.summary_metrics or {}).get("prediction_summary")
    assert ps is None
