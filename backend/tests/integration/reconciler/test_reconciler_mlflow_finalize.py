"""reconciler.jobs._finalize_mlflow_run + terminal call sites — spec § 5.5."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models.job import Job, JobStatus, JobType


def _make_job(status: JobStatus = JobStatus.RUNNING, with_mlflow: bool = True) -> Job:
    j = Job(
        id=uuid.uuid4(),
        type=JobType.TRAIN,
        status=status,
        owner_id=uuid.uuid4(),
        submitted_at=datetime.now(UTC),
        resolved_config={"yaml": ""},
    )
    if with_mlflow:
        j.mlflow_run_id = "run-abc"
        j.mlflow_experiment_id = "42"
    return j


@pytest.mark.asyncio
async def test_finalize_run_calls_update_with_failed_status() -> None:
    from app.reconciler.jobs import _finalize_mlflow_run

    j = _make_job()
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock()
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        await _finalize_mlflow_run(j, "FAILED")
    fake_client.update_run.assert_awaited_once()
    call = fake_client.update_run.call_args
    # run_id is positional in update_run signature
    assert call.args[0] == "run-abc"
    assert call.kwargs["status"] == "FAILED"
    assert isinstance(call.kwargs["end_time_ms"], int)


@pytest.mark.asyncio
async def test_finalize_run_noop_when_no_mlflow_run_id() -> None:
    from app.reconciler.jobs import _finalize_mlflow_run

    j = _make_job(with_mlflow=False)
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock()
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        await _finalize_mlflow_run(j, "FAILED")
    fake_client.update_run.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_run_swallows_mlflow_errors() -> None:
    """A flaky MLflow server must not block lolday's DB-side job status update."""
    from app.reconciler.jobs import _finalize_mlflow_run
    from app.services.mlflow_client import MlflowError

    j = _make_job()
    fake_client = MagicMock()
    fake_client.update_run = AsyncMock(side_effect=MlflowError("server unreachable"))
    with patch("app.reconciler.jobs.MlflowClient", return_value=fake_client):
        # must NOT raise
        await _finalize_mlflow_run(j, "FAILED")
