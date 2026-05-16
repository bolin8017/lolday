"""D3.3 — dev-mode seed endpoint (architecture.md §10 #12).

Idempotent fixture seeder for E2E tests. Every entity uses a UUID5
derived from a stable namespace + name so the second POST returns the
same IDs as the first.

The router is registered unconditionally; the handler raises 404 when
``settings.AUTH_DEV_MODE`` is False so production never exposes the
surface (and the existing ``Settings.validate_sso_config`` model_validator
rejects AUTH_DEV_MODE=true on a production boot — defence in depth).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import (
    DatasetConfig,
    DatasetVisibility,
    Detector,
    DetectorVersion,
    DetectorVersionStatus,
    Job,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    RegisteredModel,
    User,
)
from app.models.job import JobStatus, JobType, ResourceProfile
from app.schemas.dev_seed import SeededFixturesResponse
from app.users import current_active_user

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])

# Stable seed namespace — UUID5 derivations use this to make every fixture
# row idempotent across calls. Do not change after first use; the IDs are
# referenced from frontend specs.
_SEED_NS = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _id(name: str) -> uuid.UUID:
    return uuid.uuid5(_SEED_NS, name)


@router.post("/seed-fixtures", response_model=SeededFixturesResponse)
async def seed_fixtures(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> SeededFixturesResponse:
    """Idempotent seed for the deterministic E2E fixture set.

    Gated on ``settings.AUTH_DEV_MODE``; returns 404 in production-mode
    boots even if the router was reachable.
    """
    if not settings.AUTH_DEV_MODE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="dev surface disabled"
        )

    detector_id = _id("detector-elfrfdet")
    version_id = _id("detector-version-elfrfdet-1")
    train_ds_id = _id("dataset-train-fixture")
    test_ds_id = _id("dataset-test-fixture")
    queued_job_id = _id("job-queued-fixture")
    registered_model_id = _id("registered-model-fixture")
    model_version_id = _id("model-version-fixture")

    detector = await session.get(Detector, detector_id)
    if detector is None:
        detector = Detector(
            id=detector_id,
            name="elfrfdet-fixture",
            display_name="ELF RF Detector (fixture)",
            owner_id=user.id,
            git_url="https://github.com/bolin8017/elfrfdet.git",
        )
        session.add(detector)

    version = await session.get(DetectorVersion, version_id)
    if version is None:
        version = DetectorVersion(
            id=version_id,
            detector_id=detector_id,
            git_tag="v1.0.0-fixture",
            git_sha="0" * 40,
            harbor_image="harbor.lolday.svc:80/lolday/elfrfdet-fixture",
            image_digest=(
                "sha256:1111111111111111111111111111111111111111111111111111111111111111"
            ),
            manifest={
                "framework": "lightning",
                "stages": ["train", "evaluate", "predict"],
            },
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(version)

    for ds_id, name in (
        (train_ds_id, "fixture-train"),
        (test_ds_id, "fixture-test"),
    ):
        ds = await session.get(DatasetConfig, ds_id)
        if ds is None:
            ds = DatasetConfig(
                id=ds_id,
                name=name,
                owner_id=user.id,
                visibility=DatasetVisibility.PRIVATE,
                csv_content="sha256,label\n" + ("0" * 64) + ",benign\n",
                csv_checksum="0" * 64,
                sample_count=1,
                label_distribution={"benign": 1},
                size_bytes=80,
            )
            session.add(ds)

    job = await session.get(Job, queued_job_id)
    if job is None:
        job = Job(
            id=queued_job_id,
            type=JobType.TRAIN,
            status=JobStatus.QUEUED_BACKEND,
            owner_id=user.id,
            detector_version_id=version_id,
            train_dataset_id=train_ds_id,
            test_dataset_id=test_ds_id,
            resource_profile=ResourceProfile.GPU1,
            resolved_config={"train": {"epochs": 1}},
            idempotency_key=_id("idempotency-queued-fixture").hex,
            priority=0,
            submitted_at=datetime.now(UTC),
        )
        session.add(job)

    registered_model = await session.get(RegisteredModel, registered_model_id)
    if registered_model is None:
        registered_model = RegisteredModel(
            id=registered_model_id,
            owner_id=user.id,
            detector_id=detector_id,
            tags={"fixture": "true"},
        )
        session.add(registered_model)

    model_version = await session.get(ModelVersion, model_version_id)
    if model_version is None:
        model_version = ModelVersion(
            id=model_version_id,
            registered_model_id=registered_model_id,
            mlflow_version=1,
            mlflow_run_id="fixture-run-1",
            current_stage=ModelVersionStage.NONE,
            visibility=ModelVersionVisibility.PRIVATE,
            detector_version_id=version_id,
            source_job_id=queued_job_id,
            owner_id=user.id,
        )
        session.add(model_version)

    await session.commit()

    return SeededFixturesResponse(
        detector_id=detector_id,
        detector_version_id=version_id,
        train_dataset_id=train_ds_id,
        test_dataset_id=test_ds_id,
        queued_job_id=queued_job_id,
        registered_model_id=registered_model_id,
        model_version_id=model_version_id,
    )
