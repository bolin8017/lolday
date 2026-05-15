"""Integration tests for app.services.job_validation (D2.1 / R3 part 1).

Uses the integration-tier db_session + seed_* fixtures (real aiosqlite,
real DetectorVersion with manifest, real DatasetConfig). The unit/
tier would require excessive faking of DetectorManifest +
DetectorVersionFactory FK plumbing; integration is the right level for
this surface (it exercises real SQLAlchemy + real maldet manifest
parsing while still running in milliseconds).
"""

from __future__ import annotations

import uuid

import pytest
from app.models.job import JobType, ResourceProfile
from app.schemas.job import JobCreate
from app.services.job_validation import (
    ValidatedJob,
    ValidationError,
    validate_submission,
)


@pytest.mark.asyncio
async def test_validate_submission_train_happy_path(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )

    validated = await validate_submission(db_session, seed_user, body)

    assert isinstance(validated, ValidatedJob)
    assert str(validated.detector_version.id) == dv_id
    assert validated.train_dataset is not None
    assert str(validated.train_dataset.id) == ds_id
    assert validated.test_dataset is None
    assert validated.predict_dataset is None
    assert validated.source_model_version is None
    assert validated.job_type == JobType.TRAIN
    assert validated.resource_profile == ResourceProfile.STANDARD
    # idempotency_key is sha256 hex (64 chars)
    assert len(validated.idempotency_key) == 64


@pytest.mark.asyncio
async def test_validate_submission_unknown_detector_version_422(
    db_session, seed_user
) -> None:
    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.uuid4(),
        train_dataset_id=uuid.uuid4(),
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )

    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "detector_version_not_found"
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_validate_submission_missing_manifest_400(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    # Seed a detector_version with no manifest by directly nulling the column.
    from app.models import DetectorVersion

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    dv = await db_session.get(DetectorVersion, uuid.UUID(dv_id))
    dv.manifest = None
    await db_session.commit()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )

    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "manifest_missing"
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_submission_duplicate_within_window_409(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """Two identical submissions within the idempotency window must collide."""
    from datetime import UTC, datetime

    from app.models.job import Job, JobStatus

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 3},
    )

    first = await validate_submission(db_session, seed_user, body)

    # Persist a Job row mimicking what submit_job would land. Use a
    # NON_TERMINAL status so the duplicate-detector window check fires.
    job = Job(
        id=uuid.uuid4(),
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        owner_id=seed_user.id,
        resolved_config={"yaml": ""},
        user_params={"epochs": 3},
        idempotency_key=first.idempotency_key,
        resource_profile=ResourceProfile.STANDARD,
        submitted_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.commit()

    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "duplicate_submission"
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_validate_submission_user_params_invalid_422(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """Manifest declares train.params_schema with a required field;
    submitting without it gets a 422 / user_params_invalid."""
    from tests.fixtures.manifests import _MINIMAL_MANIFEST

    manifest = {
        **_MINIMAL_MANIFEST,
        "stages": {
            **_MINIMAL_MANIFEST["stages"],
            "train": {
                "config_class": "test.configs:TrainConfig",
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "epochs": {"type": "integer", "minimum": 1},
                    },
                    "required": ["epochs"],
                },
            },
        },
    }
    dv_id = await seed_detector_version(manifest=manifest)
    ds_id = await seed_dataset()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={},  # missing required "epochs"
    )

    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "user_params_invalid"
    assert exc.value.status_code == 422
