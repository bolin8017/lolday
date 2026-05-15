"""Integration tests for app.services.job_submission (D2.1 / R3 part 2)."""

from __future__ import annotations

import uuid

import pytest
from app.models.job import Job, JobStatus, JobType, ResourceProfile
from app.schemas.job import JobCreate
from app.services.job_submission import submit_job
from app.services.job_validation import validate_submission


@pytest.mark.asyncio
async def test_submit_job_inserts_queued_backend_row(
    db_session, seed_user, seed_detector_version, seed_dataset, mock_mlflow
) -> None:
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 2},
    )
    validated = await validate_submission(db_session, seed_user, body)

    job = await submit_job(db_session, seed_user, validated, mock_mlflow)
    await db_session.flush()

    assert isinstance(job, Job)
    assert job.status == JobStatus.QUEUED_BACKEND
    assert job.owner_id == seed_user.id
    assert job.detector_version_id == uuid.UUID(dv_id)
    assert job.train_dataset_id == uuid.UUID(ds_id)
    assert job.idempotency_key == validated.idempotency_key
    assert job.priority == 0
    assert job.mlflow_experiment_id  # populated via mock
    assert job.mlflow_run_id  # populated via mock
    # resolved_config carries the rendered YAML
    assert "yaml" in job.resolved_config


@pytest.mark.asyncio
async def test_submit_job_creates_mlflow_experiment_only_once(
    db_session, seed_user, seed_detector_version, seed_dataset, mock_mlflow
) -> None:
    """Second submission against the same DetectorVersion reuses the
    existing mlflow_experiment_id (avoid noise on the MLflow side)."""
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 1},
    )
    validated_a = await validate_submission(db_session, seed_user, body)
    job_a = await submit_job(db_session, seed_user, validated_a, mock_mlflow)
    await db_session.commit()

    body_b = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 2},  # different params → different idempotency
    )
    validated_b = await validate_submission(db_session, seed_user, body_b)
    job_b = await submit_job(db_session, seed_user, validated_b, mock_mlflow)
    await db_session.commit()

    assert job_a.mlflow_experiment_id == job_b.mlflow_experiment_id
    # Stub records each experiment_create call; we expect exactly one.
    assert len(mock_mlflow.experiment_creates) == 1


@pytest.mark.asyncio
async def test_submit_job_renders_yaml_with_user_params(
    db_session, seed_user, seed_detector_version, seed_dataset, mock_mlflow
) -> None:
    """resolved_config.yaml carries user params + mlflow run_id from the stub."""
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 5, "lr": 0.01},
    )
    validated = await validate_submission(db_session, seed_user, body)
    job = await submit_job(db_session, seed_user, validated, mock_mlflow)
    await db_session.flush()

    assert "yaml" in job.resolved_config
    rendered = job.resolved_config["yaml"]
    assert "epochs: 5" in rendered
    assert "lr: 0.01" in rendered
    # The stub's run_id is set on the Job and threaded through YAML
    assert job.mlflow_run_id in rendered
