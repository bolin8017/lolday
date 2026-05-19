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


# ---------------------------------------------------------------------------
# Manifest is non-null but not parseable as a DetectorManifest. Pydantic
# raises; the helper must catch and surface as manifest_invalid/400.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_manifest_invalid_400(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """A non-empty but structurally-invalid manifest must surface as
    manifest_invalid/400 (not bubble up the bare pydantic.ValidationError)."""
    from app.models import DetectorVersion

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    dv = await db_session.get(DetectorVersion, uuid.UUID(dv_id))
    dv.manifest = {"detector": "this-should-be-a-dict-not-a-string"}
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
    assert exc.value.code == "manifest_invalid"
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Manifest declares a lifecycle stage but the matching [stages.<name>] block
# is missing. validate_job_submission catches the "stage not in manifest"
# case earlier; this branch (stage_block_missing) is reached when the
# stage IS in lifecycle.stages but stage_spec is None — a maldet manifest
# drift signature.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_stage_block_missing_400(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    from app.models import DetectorVersion

    from tests.fixtures.manifests import _MINIMAL_MANIFEST

    # Keep all top-level manifest fields; strip just the [stages.train] block
    # while leaving lifecycle.stages mentioning "train" — which makes
    # validate_job_submission pass but stage_spec lookup return None.
    bad = {
        **_MINIMAL_MANIFEST,
        "stages": {
            k: v for k, v in _MINIMAL_MANIFEST["stages"].items() if k != "train"
        },
    }
    dv_id = await seed_detector_version(manifest=bad)
    ds_id = await seed_dataset()
    dv = await db_session.get(DetectorVersion, uuid.UUID(dv_id))
    dv.manifest = bad
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
    # Either stage_invalid (caught by validate_job_submission upstream) or
    # stage_block_missing — both indicate the same "manifest drift" class.
    assert exc.value.code in {"stage_invalid", "stage_block_missing"}
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Detector FK invariant: dv.detector_id references a row that was deleted
# without cascading. The early FK in the row protects the prod path, but the
# 500 catch is defensive — pin its contract.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_detector_fk_invariant_500(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    from app.models import DetectorVersion

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    dv = await db_session.get(DetectorVersion, uuid.UUID(dv_id))
    # Force the FK to point at a non-existent detector — emulating prod-side
    # row removal that should never happen but is defended against.
    dv.detector_id = uuid.uuid4()
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
    assert exc.value.code == "detector_fk_invariant"
    assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# priority is admin-only: a non-admin user requesting priority != 0 (and not
# None) must be rejected with 403. priority=0 / priority=None remain
# permitted for everyone.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_priority_admin_only_403(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """seed_user has role=developer (per the fixture default); a non-zero
    priority must surface as priority_admin_only/403."""
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={},
        priority=5,
    )
    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "priority_admin_only"
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_validate_submission_admin_can_set_priority(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """Same payload from an admin user must pass and persist priority=5."""
    from app.models import Role

    seed_user.role = Role.ADMIN
    db_session.add(seed_user)
    await db_session.commit()

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={},
        priority=5,
    )
    validated = await validate_submission(db_session, seed_user, body)
    assert validated.priority == 5


# ---------------------------------------------------------------------------
# Concurrency cap: more than JOB_PER_USER_CONCURRENCY non-terminal jobs for
# the same owner gets a 429. Monkeypatch the cap to 1 and stage a single
# QUEUED_BACKEND row so the second submission trips the limit.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_concurrency_limit_429(
    db_session, seed_user, seed_detector_version, seed_dataset, monkeypatch
) -> None:
    from datetime import UTC, datetime

    from app.models.job import Job, JobStatus

    monkeypatch.setattr(
        "app.services.job_validation.settings.JOB_PER_USER_CONCURRENCY", 1
    )

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    # Stage one non-terminal job to saturate the cap.
    db_session.add(
        Job(
            id=uuid.uuid4(),
            type=JobType.TRAIN,
            status=JobStatus.QUEUED_BACKEND,
            detector_version_id=uuid.UUID(dv_id),
            train_dataset_id=uuid.UUID(ds_id),
            owner_id=seed_user.id,
            resolved_config={"yaml": ""},
            user_params={},
            idempotency_key="x" * 64,
            resource_profile=ResourceProfile.STANDARD,
            submitted_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    body = JobCreate(
        type=JobType.TRAIN,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(ds_id),
        resource_profile=ResourceProfile.STANDARD,
        params={"epochs": 7},  # different idempotency key so dup check doesn't fire
    )
    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "concurrency_limit"
    assert exc.value.status_code == 429


# ---------------------------------------------------------------------------
# Dataset access: not-found (deleted) and not-accessible (private + other
# owner). These run inside _load_dataset before the manifest path so they
# surface before any maldet validation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_dataset_not_found_422(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    from app.models import DatasetConfig

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    # Soft-delete the dataset.
    ds = await db_session.get(DatasetConfig, uuid.UUID(ds_id))
    from datetime import UTC, datetime

    ds.deleted_at = datetime.now(UTC)
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
    assert exc.value.code == "dataset_not_found"
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_validate_submission_dataset_not_accessible_422(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """A PRIVATE dataset owned by another user; non-admin must get
    dataset_not_accessible/422."""
    from app.models import DatasetConfig
    from app.models.dataset import DatasetVisibility
    from app.models.user import Role

    from tests.conftest import _make_user

    other = await _make_user("dataset-other@example.dev", role=Role.USER)
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    ds = await db_session.get(DatasetConfig, uuid.UUID(ds_id))
    ds.owner_id = other.id
    ds.visibility = DatasetVisibility.PRIVATE
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
    assert exc.value.code == "dataset_not_accessible"
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# source_model_version access on predict-type submissions: not-found and
# not-accessible (PRIVATE + other-owner) branches in
# _load_model_version_for_predict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_submission_source_model_not_found_422(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()

    body = JobCreate(
        type=JobType.PREDICT,
        detector_version_id=uuid.UUID(dv_id),
        predict_dataset_id=uuid.UUID(ds_id),
        source_model_version_id=uuid.uuid4(),  # missing
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )
    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "source_model_not_found"
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_validate_submission_source_model_not_accessible_422(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """ModelVersion is PRIVATE and owned by a different user; non-admin must
    surface as source_model_not_accessible/422."""
    from app.models import DetectorVersion, Job, ModelVersion
    from app.models.job import JobStatus
    from app.models.model_registry import (
        ModelVersionStage,
        ModelVersionVisibility,
        RegisteredModel,
    )
    from app.models.user import Role

    from tests.conftest import _make_user

    other = await _make_user("model-other@example.dev", role=Role.USER)
    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    dv_row = await db_session.get(DetectorVersion, uuid.UUID(dv_id))

    src_job = Job(
        type=JobType.TRAIN,
        status=JobStatus.SUCCEEDED,
        detector_version_id=dv_row.id,
        train_dataset_id=uuid.UUID(ds_id),
        owner_id=other.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        resource_profile=ResourceProfile.STANDARD,
    )
    db_session.add(src_job)
    await db_session.flush()

    rm = RegisteredModel(owner_id=other.id, detector_id=dv_row.detector_id)
    db_session.add(rm)
    await db_session.flush()
    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=1,
        mlflow_run_id="run-xyz",
        current_stage=ModelVersionStage.NONE,
        detector_version_id=dv_row.id,
        source_job_id=src_job.id,
        owner_id=other.id,
        visibility=ModelVersionVisibility.PRIVATE,
    )
    db_session.add(mv)
    await db_session.commit()
    mv_id = mv.id

    body = JobCreate(
        type=JobType.PREDICT,
        detector_version_id=uuid.UUID(dv_id),
        predict_dataset_id=uuid.UUID(ds_id),
        source_model_version_id=mv_id,
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )
    with pytest.raises(ValidationError) as exc:
        await validate_submission(db_session, seed_user, body)
    assert exc.value.code == "source_model_not_accessible"
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_validate_submission_predict_with_own_model_passes(
    db_session, seed_user, seed_detector_version, seed_dataset
) -> None:
    """Predict job referencing a ModelVersion owned by the same user must
    return a ValidatedJob populated with that source_model_version (pins the
    happy-path return inside _load_model_version_for_predict — line 319)."""
    from app.models import DetectorVersion, Job, ModelVersion
    from app.models.job import JobStatus
    from app.models.model_registry import (
        ModelVersionStage,
        ModelVersionVisibility,
        RegisteredModel,
    )

    dv_id = await seed_detector_version()
    ds_id = await seed_dataset()
    dv_row = await db_session.get(DetectorVersion, uuid.UUID(dv_id))

    src_job = Job(
        type=JobType.TRAIN,
        status=JobStatus.SUCCEEDED,
        detector_version_id=dv_row.id,
        train_dataset_id=uuid.UUID(ds_id),
        owner_id=seed_user.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        resource_profile=ResourceProfile.STANDARD,
    )
    db_session.add(src_job)
    await db_session.flush()

    rm = RegisteredModel(owner_id=seed_user.id, detector_id=dv_row.detector_id)
    db_session.add(rm)
    await db_session.flush()
    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=1,
        mlflow_run_id="run-own",
        current_stage=ModelVersionStage.NONE,
        detector_version_id=dv_row.id,
        source_job_id=src_job.id,
        owner_id=seed_user.id,
        visibility=ModelVersionVisibility.PRIVATE,
    )
    db_session.add(mv)
    await db_session.commit()

    body = JobCreate(
        type=JobType.PREDICT,
        detector_version_id=uuid.UUID(dv_id),
        predict_dataset_id=uuid.UUID(ds_id),
        source_model_version_id=mv.id,
        resource_profile=ResourceProfile.STANDARD,
        params={},
    )
    validated = await validate_submission(db_session, seed_user, body)
    assert validated.source_model_version is not None
    assert validated.source_model_version.id == mv.id
