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
from app.models import Job
from app.models.job import JobStatus, JobType, ResourceProfile
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_predict_job(
    session: AsyncSession,
    *,
    owner_id: _uuid.UUID,
    detector_version_id: _uuid.UUID,
    mlflow_run_id: str | None = "run-123",
    started_at: _dt.datetime | None = None,
    finished_at: _dt.datetime | None = None,
) -> Job:
    """Build a SUCCEEDED predict Job row. owner_id + detector_version_id
    are FKs that must come from the `reconciler_owner` +
    `reconciler_detector_version` fixtures (#530)."""
    job = Job(
        id=_uuid.uuid4(),
        type=JobType.PREDICT,
        status=JobStatus.SUCCEEDED,
        owner_id=owner_id,
        detector_version_id=detector_version_id,
        resource_profile=ResourceProfile.STANDARD,
        resolved_config={},
        idempotency_key="test-" + _uuid.uuid4().hex,
        submitted_at=_dt.datetime.now(_dt.UTC),
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
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    started = _dt.datetime.now(_dt.UTC)
    finished = started + _dt.timedelta(seconds=12)
    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
        started_at=started,
        finished_at=finished,
    )

    csv = "file_name,pred_label\nA,Malware\nB,Benign\nC,Malware\nD,Malware\n"
    with patch(
        "app.reconciler.projections._read_mlflow_artifact",
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
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
    )
    with patch(
        "app.reconciler.projections._read_mlflow_artifact",
        new=AsyncMock(side_effect=FileNotFoundError("no predictions.csv")),
    ):
        from app.reconciler import _project_prediction_summary

        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    ps = (job.summary_metrics or {}).get("prediction_summary")
    assert ps is None


@pytest.mark.asyncio
async def test_project_prediction_summary_skips_when_mlflow_run_id_missing(
    db_session: AsyncSession,
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    """A predict job without an mlflow_run_id (e.g. the run wasn't created
    yet at finalize) returns early — no artifact fetch, no summary written."""
    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
        mlflow_run_id=None,
    )
    from app.reconciler import _project_prediction_summary

    # No patch on _read_mlflow_artifact — the early return must not reach it.
    await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    assert (job.summary_metrics or {}).get("prediction_summary") is None


@pytest.mark.asyncio
async def test_project_prediction_summary_swallows_artifact_read_error(
    db_session: AsyncSession,
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    """A non-404 failure (e.g. HTTP 500, network error) increments the
    `prediction_summary_artifact_read` BACKEND_ERRORS stage and returns
    without writing prediction_summary."""
    from prometheus_client import REGISTRY

    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
    )

    def _get(stage: str) -> float:
        return (
            REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
            or 0.0
        )

    before = _get("prediction_summary_artifact_read")

    with patch(
        "app.reconciler.projections._read_mlflow_artifact",
        new=AsyncMock(side_effect=RuntimeError("synthetic 500")),
    ):
        from app.reconciler import _project_prediction_summary

        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    assert (job.summary_metrics or {}).get("prediction_summary") is None
    assert _get("prediction_summary_artifact_read") == before + 1.0


@pytest.mark.asyncio
async def test_project_prediction_summary_swallows_csv_parse_error(
    db_session: AsyncSession,
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    """A `csv.Error` while iterating predictions.csv increments the
    `prediction_summary_csv_parse` BACKEND_ERRORS stage and returns without
    writing prediction_summary. csv.Error is raised lazily during iteration
    of certain malformed quoting patterns, so we patch csv.DictReader to a
    class whose __iter__ raises deterministically."""
    import csv as _csv

    from prometheus_client import REGISTRY

    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
    )

    def _get(stage: str) -> float:
        return (
            REGISTRY.get_sample_value("lolday_backend_errors_total", {"stage": stage})
            or 0.0
        )

    before = _get("prediction_summary_csv_parse")

    class _FailingReader:
        def __init__(self, *_a, **_kw):
            self.fieldnames = ["file_name", "pred_label"]

        def __iter__(self):
            raise _csv.Error("synthetic malformed quoting")

    with (
        patch(
            "app.reconciler.projections._read_mlflow_artifact",
            new=AsyncMock(return_value="file_name,pred_label\nA,M\n"),
        ),
        patch("app.reconciler.projections.csv.DictReader", _FailingReader),
    ):
        from app.reconciler import _project_prediction_summary

        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    assert (job.summary_metrics or {}).get("prediction_summary") is None
    assert _get("prediction_summary_csv_parse") == before + 1.0


@pytest.mark.asyncio
async def test_project_prediction_summary_skips_csv_without_pred_label_column(
    db_session: AsyncSession,
    reconciler_owner,
    reconciler_detector_version,
) -> None:
    """A CSV whose header lacks `pred_label` is silently skipped — better to
    render no card than wrong counts."""
    job = await _make_predict_job(
        db_session,
        owner_id=reconciler_owner,
        detector_version_id=reconciler_detector_version.id,
    )
    # Header has file_name + score but no pred_label column.
    csv_text = "file_name,score\nA,0.91\nB,0.42\n"
    with patch(
        "app.reconciler.projections._read_mlflow_artifact",
        new=AsyncMock(return_value=csv_text),
    ):
        from app.reconciler import _project_prediction_summary

        await _project_prediction_summary(db_session, job)
    await db_session.refresh(job)

    assert (job.summary_metrics or {}).get("prediction_summary") is None


@pytest.mark.asyncio
async def test_read_mlflow_artifact_round_trip():
    """Round-trip through the real httpx-based artifact reader: run-get
    returns the artifact_uri prefix, then the artifacts download returns the
    text body. Mocked via respx so no real MLflow contact."""
    import httpx
    import respx
    from app.config import settings
    from app.reconciler.projections import _read_mlflow_artifact

    base = settings.MLFLOW_TRACKING_URI

    with respx.mock:
        respx.get(f"{base}/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"artifact_uri": "mlflow-artifacts:/0/abc/artifacts"}
                    }
                },
            )
        )
        respx.get(
            f"{base}/api/2.0/mlflow-artifacts/artifacts/0/abc/artifacts/predictions.csv"
        ).mock(return_value=httpx.Response(200, text="file_name,pred_label\nA,M\n"))

        text = await _read_mlflow_artifact("run-abc", "predictions.csv")
    assert text.startswith("file_name,pred_label")


@pytest.mark.asyncio
async def test_read_mlflow_artifact_404_raises_filenotfound():
    """A 404 on the artifact GET surfaces as FileNotFoundError so callers can
    silently skip jobs that legitimately lack the file."""
    import httpx
    import respx
    from app.config import settings
    from app.reconciler.projections import _read_mlflow_artifact

    base = settings.MLFLOW_TRACKING_URI

    with respx.mock:
        respx.get(f"{base}/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"artifact_uri": "mlflow-artifacts:/0/abc/artifacts"}
                    }
                },
            )
        )
        respx.get(
            f"{base}/api/2.0/mlflow-artifacts/artifacts/0/abc/artifacts/predictions.csv"
        ).mock(return_value=httpx.Response(404))

        with pytest.raises(FileNotFoundError):
            await _read_mlflow_artifact("run-abc", "predictions.csv")


@pytest.mark.asyncio
async def test_read_mlflow_artifact_unexpected_uri_scheme_raises():
    """Defence-in-depth: an artifact_uri without the `mlflow-artifacts:/`
    prefix indicates an MLflow misconfig (file:// or s3:// without the
    proxy); raise RuntimeError instead of silently doing the wrong thing."""
    import httpx
    import respx
    from app.config import settings
    from app.reconciler.projections import _read_mlflow_artifact

    base = settings.MLFLOW_TRACKING_URI

    with respx.mock:
        respx.get(f"{base}/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(
                200,
                json={
                    "run": {
                        "info": {"artifact_uri": "s3://wrong-scheme/0/abc/artifacts"}
                    }
                },
            )
        )

        with pytest.raises(RuntimeError, match="unexpected artifact_uri scheme"):
            await _read_mlflow_artifact("run-abc", "predictions.csv")
