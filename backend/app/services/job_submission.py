"""Persist a ValidatedJob into a Job row + MLflow experiment/run.

Extracted from ``app.routers.jobs.POST /jobs`` (D2.1 / R3) so the router
becomes a thin HTTP adapter. The split between :func:`submit_job` and
:func:`app.services.job_dispatch.dispatch_job_to_volcano` is preserved
from the pre-R3 flow: ``submit_job`` writes the DB row with
``status=queued_backend``; the FIFO scheduler reconciler later calls
``dispatch_job_to_volcano`` to materialise the Volcano vcjob.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User
from app.models.job import (
    RESOURCE_PROFILE_GPU_COUNT,
    Job,
    JobStatus,
)
from app.services.job_config import JobConfigRenderer
from app.services.job_validation import ValidatedJob, ValidationError
from app.services.mlflow_client import MlflowClient, MlflowError

logger = logging.getLogger(__name__)


async def submit_job(
    session: AsyncSession,
    user: User,
    validated: ValidatedJob,
    mlflow_client: MlflowClient,
) -> Job:
    """Create the MLflow experiment/run + insert a queued_backend Job row.

    The caller (router or reconciler) owns the transaction boundary — this
    function only ``session.add()``s and may ``session.flush()`` to obtain
    the auto-generated MLflow experiment id for the same DetectorVersion.

    On YAML render failure (user params collide with reserved namespace),
    raises :class:`app.services.job_validation.ValidationError` with code
    ``config_render_failed`` (400) — same one-error-type contract the
    router translates.
    """
    job_id = uuid.uuid4()
    dv = validated.detector_version
    det = validated.detector

    detector_version_label = f"{det.name}/{dv.git_tag}"
    exp_name = f"{user.handle}/{detector_version_label}"
    run_name = f"{validated.job_type.value}-{job_id.hex[:8]}"

    newly_created_experiment = False
    if not dv.mlflow_experiment_id:
        dv.mlflow_experiment_id = await mlflow_client.get_or_create_experiment(exp_name)
        newly_created_experiment = True
        await session.flush()

    if newly_created_experiment:
        note = (
            f"**Detector**: `{det.name}` @ `{dv.git_tag}`\n\n"
            f"**Owner**: `{user.handle}`\n\n"
            f"**Description**: {(det.description or '_no description_')}\n\n"
            f"**Maldet framework**: `{dv.maldet_version or '_unknown_'}`\n"
        )
        for k, v in (
            ("mlflow.note.content", note),
            ("lolday.detector_id", str(det.id)),
            ("lolday.detector_version_id", str(dv.id)),
            ("lolday.owner_id", str(user.id)),
            ("lolday.owner_handle", user.handle),
        ):
            try:
                await mlflow_client.set_experiment_tag(dv.mlflow_experiment_id, k, v)
            except MlflowError as exc:
                logger.warning(
                    "set_experiment_tag failed for %s key=%s: %s",
                    dv.mlflow_experiment_id,
                    k,
                    exc,
                )

    gpu_count_val = RESOURCE_PROFILE_GPU_COUNT[validated.resource_profile]
    run_id = await mlflow_client.create_run(
        dv.mlflow_experiment_id,
        start_time_ms=int(time.time() * 1000),
        tags=[
            {"key": "mlflow.runName", "value": run_name},
            {"key": "mlflow.source.name", "value": detector_version_label},
            {"key": "mlflow.source.type", "value": "JOB"},
            {"key": "mlflow.source.git.commit", "value": dv.git_sha or ""},
            {"key": "maldet.action", "value": validated.job_type.value},
            {"key": "lolday.job_id", "value": str(job_id)},
            {"key": "lolday.user", "value": user.handle},
            {"key": "lolday.user_id", "value": str(user.id)},
            {"key": "lolday.detector_version", "value": detector_version_label},
            {"key": "lolday.detector_version_id", "value": str(dv.id)},
            {"key": "lolday.detector_image_digest", "value": dv.image_digest or ""},
            {"key": "lolday.maldet_version", "value": dv.maldet_version or ""},
            {
                "key": "lolday.resource_profile",
                "value": validated.resource_profile.value,
            },
            {"key": "lolday.gpu_count", "value": str(gpu_count_val)},
            {
                "key": "lolday.train_dataset_id",
                "value": str(validated.train_dataset.id)
                if validated.train_dataset
                else "",
            },
            {
                "key": "lolday.test_dataset_id",
                "value": str(validated.test_dataset.id)
                if validated.test_dataset
                else "",
            },
            {
                "key": "lolday.predict_dataset_id",
                "value": str(validated.predict_dataset.id)
                if validated.predict_dataset
                else "",
            },
            {
                "key": "lolday.source_model_version_id",
                "value": str(validated.source_model_version.id)
                if validated.source_model_version
                else "",
            },
        ],
    )

    renderer = JobConfigRenderer(
        samples_root=settings.SAMPLES_ROOT,
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    try:
        resolved_yaml = renderer.render_config_yaml(
            stage=validated.job_type.value,
            user_params=validated.params,
            mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
            mlflow_run_id=run_id,
            mlflow_experiment_id=dv.mlflow_experiment_id,
            lolday_meta={
                "train_dataset_id": str(validated.train_dataset.id)
                if validated.train_dataset
                else "",
                "test_dataset_id": str(validated.test_dataset.id)
                if validated.test_dataset
                else "",
                "predict_dataset_id": str(validated.predict_dataset.id)
                if validated.predict_dataset
                else "",
                "source_model_version_id": str(validated.source_model_version.id)
                if validated.source_model_version
                else "",
                "job_id": str(job_id),
            },
        )
    except ValueError as exc:
        raise ValidationError("config_render_failed", str(exc), 400) from exc

    job = Job(
        id=job_id,
        type=validated.job_type,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=dv.id,
        train_dataset_id=validated.train_dataset.id
        if validated.train_dataset
        else None,
        test_dataset_id=validated.test_dataset.id if validated.test_dataset else None,
        predict_dataset_id=validated.predict_dataset.id
        if validated.predict_dataset
        else None,
        source_model_version_id=validated.source_model_version.id
        if validated.source_model_version
        else None,
        owner_id=user.id,
        resolved_config={"yaml": resolved_yaml},
        user_params=validated.params,
        mlflow_experiment_id=dv.mlflow_experiment_id,
        mlflow_run_id=run_id,
        idempotency_key=validated.idempotency_key,
        resource_profile=validated.resource_profile,
        active_deadline_seconds=validated.active_deadline_seconds,
        priority=validated.priority,
    )
    session.add(job)
    return job
