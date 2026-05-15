"""Pure-function payload validation for the job-submission flow.

Extracted from ``app.routers.jobs.POST /jobs`` (D2.1 / R3) so the
validation can be exercised by hypothesis (``unit/invariants/``) and
schemathesis (``contract/openapi/``) without spinning up a TestClient.

Inputs: the request-scoped ``AsyncSession``, the authenticated ``User``,
and the parsed Pydantic ``JobCreate`` body. Output: a frozen
``ValidatedJob`` dataclass. Mutations (``session.add`` / commit) happen
in ``app.services.job_submission.submit_job``; K8s side-effects happen
in ``app.services.job_dispatch.dispatch_job_to_volcano``.

Errors surface as :class:`ValidationError` carrying the same
``(code, message, status_code)`` tuple the router used to map directly
to ``HTTPException`` — so the router catches one type and translates
mechanically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pydantic
from maldet.manifest import DetectorManifest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    DatasetConfig,
    Detector,
    DetectorVersion,
    ModelVersion,
    User,
)
from app.models.dataset import DatasetVisibility
from app.models.job import (
    NON_TERMINAL_STATUSES,
    Job,
    JobType,
    ResourceProfile,
)
from app.models.model_registry import ModelVersionVisibility
from app.schemas.job import JobCreate
from app.services.dataset import DatasetIntegrityError, parse_csv, spot_check_samples
from app.services.job_config import compute_idempotency_key
from app.services.jobs_params_validate import (
    UserParamsRejected,
    validate_user_params,
)
from app.services.validator import JobSubmissionError, validate_job_submission


class ValidationError(Exception):
    """Raised when a ``JobCreate`` payload cannot become a ``Job`` row.

    Carries the HTTP status the router will translate to. Tests assert
    on ``code`` (stable contract) instead of message text.
    """

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedJob:
    detector_version: DetectorVersion
    detector: Detector
    manifest: DetectorManifest
    train_dataset: DatasetConfig | None
    test_dataset: DatasetConfig | None
    predict_dataset: DatasetConfig | None
    source_model_version: ModelVersion | None
    params: dict[str, Any]
    resource_profile: ResourceProfile
    job_type: JobType
    idempotency_key: str
    priority: int
    active_deadline_seconds: int | None


async def validate_submission(
    session: AsyncSession,
    user: User,
    body: JobCreate,
) -> ValidatedJob:
    """Validate a job submission against the DB + the detector's manifest.

    Order mirrors the pre-R3 inline flow in ``routers/jobs.py``: load
    detector_version → load dataset refs → load source model → parse
    manifest → manifest pre-flight → stage block check → user-params
    schema check → idempotency window → concurrency cap → detector FK
    invariant → dataset integrity spot-check (skipped if SAMPLES_LOCAL_ROOT
    doesn't exist locally).
    """
    dv = await session.get(DetectorVersion, body.detector_version_id)
    if dv is None:
        raise ValidationError(
            "detector_version_not_found", "detector_version not found", 422
        )

    train_ds = await _load_dataset(
        session, user, body.train_dataset_id, "train_dataset_id"
    )
    test_ds = await _load_dataset(
        session, user, body.test_dataset_id, "test_dataset_id"
    )
    predict_ds = await _load_dataset(
        session, user, body.predict_dataset_id, "predict_dataset_id"
    )

    source_model = await _load_model_version_for_predict(
        session, user, body.source_model_version_id
    )

    if dv.manifest is None:
        raise ValidationError(
            "manifest_missing",
            "detector_version has no maldet manifest (older detector?); rebuild the detector with maldet >= 1.1",
            400,
        )
    try:
        manifest_model = DetectorManifest.model_validate(dv.manifest)
    except pydantic.ValidationError as exc:
        raise ValidationError(
            "manifest_invalid", f"stored manifest invalid: {exc}", 400
        ) from exc

    try:
        validate_job_submission(
            manifest=manifest_model,
            resource_profile=body.resource_profile,
            dataset_contract="sample_csv",
            stage=body.type.value,
        )
    except JobSubmissionError as exc:
        raise ValidationError("stage_invalid", str(exc), 400) from exc

    stage_spec = manifest_model.stages.get(body.type.value)
    if stage_spec is None:
        raise ValidationError(
            "stage_block_missing",
            f"manifest declares lifecycle stage {body.type.value!r} but missing "
            f"[stages.{body.type.value}] block; rebuild detector with maldet ≥ 1.1",
            400,
        )

    try:
        validate_user_params(params=body.params, schema=stage_spec.params_schema)
    except UserParamsRejected as exc:
        raise ValidationError("user_params_invalid", str(exc), 422) from exc

    idem_key = compute_idempotency_key(
        user_id=str(user.id),
        detector_version_id=str(dv.id),
        job_type=body.type.value,
        train_ds=str(train_ds.id) if train_ds else None,
        test_ds=str(test_ds.id) if test_ds else None,
        predict_ds=str(predict_ds.id) if predict_ds else None,
        source_model=str(source_model.id) if source_model else None,
        params=body.params,
    )
    window_start = datetime.now(UTC) - timedelta(
        seconds=settings.JOB_IDEMPOTENCY_WINDOW_SECONDS
    )
    dup = (
        await session.execute(
            select(Job).where(
                Job.idempotency_key == idem_key,
                Job.submitted_at >= window_start,
                Job.status.in_(NON_TERMINAL_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ValidationError(
            "duplicate_submission",
            f"duplicate submission; existing job: {dup.id}",
            409,
        )

    in_flight = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.owner_id == user.id,
                Job.status.in_(NON_TERMINAL_STATUSES),
            )
        )
    ).scalar_one()
    if in_flight >= settings.JOB_PER_USER_CONCURRENCY:
        raise ValidationError(
            "concurrency_limit",
            f"in-flight limit ({settings.JOB_PER_USER_CONCURRENCY}) reached",
            429,
        )

    det = await session.get(Detector, dv.detector_id)
    if det is None:
        raise ValidationError(
            "detector_fk_invariant",
            f"FK invariant violated: DetectorVersion {dv.id} references missing Detector {dv.detector_id}",
            500,
        )

    # Priority is admin-only; non-admin requesting non-zero gets a clean 403.
    requested_priority = body.priority
    if requested_priority not in (None, 0) and user.role.value != "admin":
        raise ValidationError(
            "priority_admin_only", "priority field is admin-only", 403
        )
    priority_to_persist = requested_priority if requested_priority is not None else 0

    samples_root = Path(settings.SAMPLES_LOCAL_ROOT)
    if samples_root.exists():
        try:
            for ds in (train_ds, test_ds, predict_ds):
                if ds is None:
                    continue
                parsed = parse_csv(ds.csv_content)
                spot_check_samples(
                    file_names=parsed.file_names,
                    labels=parsed.labels,
                    samples_root=samples_root,
                    sample_count=settings.DATASET_SPOT_CHECK_COUNT,
                    missing_threshold=settings.DATASET_SPOT_CHECK_MISSING_THRESHOLD,
                )
        except DatasetIntegrityError as exc:
            raise ValidationError(
                "dataset_integrity_failed",
                f"dataset_integrity_failed: {exc}",
                422,
            ) from exc

    return ValidatedJob(
        detector_version=dv,
        detector=det,
        manifest=manifest_model,
        train_dataset=train_ds,
        test_dataset=test_ds,
        predict_dataset=predict_ds,
        source_model_version=source_model,
        params=body.params,
        resource_profile=body.resource_profile,
        job_type=body.type,
        idempotency_key=idem_key,
        priority=priority_to_persist,
        active_deadline_seconds=body.active_deadline_seconds,
    )


async def _load_dataset(
    session: AsyncSession,
    user: User,
    ds_id: uuid.UUID | None,
    field: str,
) -> DatasetConfig | None:
    if ds_id is None:
        return None
    ds = await session.get(DatasetConfig, ds_id)
    if ds is None or ds.deleted_at is not None:
        raise ValidationError(
            "dataset_not_found",
            f"{field}: dataset not found or deleted",
            422,
        )
    if (
        ds.visibility == DatasetVisibility.PRIVATE
        and ds.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise ValidationError(
            "dataset_not_accessible",
            f"{field}: dataset not accessible",
            422,
        )
    return ds


async def _load_model_version_for_predict(
    session: AsyncSession,
    user: User,
    mv_id: uuid.UUID | None,
) -> ModelVersion | None:
    if mv_id is None:
        return None
    mv = await session.get(ModelVersion, mv_id)
    if mv is None:
        raise ValidationError(
            "source_model_not_found",
            "source_model_version not found",
            422,
        )
    if (
        mv.visibility == ModelVersionVisibility.PRIVATE
        and mv.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise ValidationError(
            "source_model_not_accessible",
            "source_model_version not accessible",
            422,
        )
    return mv
