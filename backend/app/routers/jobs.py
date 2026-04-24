import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.metrics import BACKEND_ERRORS
from app.users import current_active_user
from app.models import DatasetConfig, DetectorVersion, Job, ModelVersion, User
from app.models.dataset import DatasetVisibility
from app.models.job import JobStatus, JobType, NON_TERMINAL_STATUSES
from app.schemas.job import JobCreate, JobList, JobRead, JobSummary
from app.services.dataset import DatasetIntegrityError, spot_check_samples, parse_csv
from app.services.job_config import (
    JobConfigRenderer,
    compute_idempotency_key,
    resolve_source_model_path,
)
from app.services.validator import JobSubmissionError, validate_job_submission
from app.services.job_spec import build_job_token_secret, build_volcano_job_manifest
from app.services.job_tokens import generate_token, hash_token
from app.services.cluster_status import get_job_queue_position
from app.services.rate_limit import rate_limit_user
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)
from app.services.mlflow_client import MlflowClient

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_mlflow_client() -> MlflowClient:
    return MlflowClient(settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS)


async def _load_dataset(
    ds_id: uuid.UUID | None, session: AsyncSession, user: User, field: str
) -> DatasetConfig | None:
    if ds_id is None:
        return None
    ds = await session.get(DatasetConfig, ds_id)
    if ds is None or ds.deleted_at is not None:
        raise HTTPException(status_code=422, detail=f"{field}: dataset not found or deleted")
    if (
        ds.visibility == DatasetVisibility.PRIVATE
        and ds.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise HTTPException(status_code=422, detail=f"{field}: dataset not accessible")
    return ds


def _extract_defaults(schema: dict) -> dict:
    """Pull default values out of a Pydantic-generated JSON schema."""
    defaults: dict = {}
    properties = schema.get("properties", {})
    defs = schema.get("$defs", {})

    for key, prop in properties.items():
        if "default" in prop:
            defaults[key] = prop["default"]
        elif "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            ref_schema = defs.get(ref_name, {})
            nested = _extract_defaults(ref_schema)
            if nested:
                defaults[key] = nested
    return defaults


def _detector_cli(det_name: str) -> str:
    """Detector CLI = detector slug (Phase 3 convention from pyproject.scripts)."""
    return det_name


def _registered_model_name(det_name: str) -> str:
    return det_name


@router.post(
    "",
    status_code=202,
    response_model=JobRead,
    dependencies=[Depends(rate_limit_user("jobs_create", 30, 60))],
)
async def create_job(
    body: JobCreate,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    # 1. detector_version
    dv = await session.get(DetectorVersion, body.detector_version_id)
    if dv is None:
        raise HTTPException(status_code=422, detail="detector_version not found")

    # 2. dataset refs
    train_ds = await _load_dataset(body.train_dataset_id, session, user, "train_dataset_id")
    test_ds = await _load_dataset(body.test_dataset_id, session, user, "test_dataset_id")
    predict_ds = await _load_dataset(body.predict_dataset_id, session, user, "predict_dataset_id")

    # 3. source model
    source_run_id = None
    source_model = None
    if body.source_model_version_id is not None:
        source_model = await session.get(ModelVersion, body.source_model_version_id)
        if source_model is None:
            raise HTTPException(status_code=422, detail="source_model_version not found")
        source_run_id = source_model.mlflow_run_id

    # 4. params schema validation
    try:
        jsonschema.validate(instance=body.params, schema=dv.config_schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(status_code=422, detail=f"params invalid: {e.message}")

    # 4b. Manifest pre-flight (resource_profile / dataset_contract / stage)
    if dv.manifest is None:
        raise HTTPException(
            status_code=400,
            detail="detector_version has no maldet manifest (older detector?); rebuild the detector with maldet v1.0+",
        )
    try:
        from maldet.manifest import DetectorManifest
        manifest_model = DetectorManifest.model_validate(dv.manifest)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"stored manifest invalid: {exc}") from exc
    try:
        validate_job_submission(
            manifest=manifest_model,
            resource_profile=body.resource_profile,
            dataset_contract="sample_csv",
            stage=body.type.value,
        )
    except JobSubmissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 5. Idempotency
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
    window_start = datetime.now(timezone.utc) - timedelta(seconds=settings.JOB_IDEMPOTENCY_WINDOW_SECONDS)
    dup = (await session.execute(
        select(Job).where(
            Job.idempotency_key == idem_key,
            Job.submitted_at >= window_start,
            Job.status.in_(NON_TERMINAL_STATUSES),
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"duplicate submission; existing job: {dup.id}")

    # 6. Concurrency
    in_flight = (await session.execute(
        select(func.count()).select_from(Job).where(
            Job.owner_id == user.id,
            Job.status.in_(NON_TERMINAL_STATUSES),
        )
    )).scalar_one()
    if in_flight >= settings.JOB_PER_USER_CONCURRENCY:
        raise HTTPException(status_code=429, detail=f"in-flight limit ({settings.JOB_PER_USER_CONCURRENCY}) reached")

    # 7. Integrity spot-check (only if samples dir exists locally)
    from pathlib import Path
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
        except DatasetIntegrityError as e:
            raise HTTPException(status_code=422, detail=f"dataset_integrity_failed: {e}")

    # 8. MLflow experiment + run
    client = _get_mlflow_client()
    exp_name = f"detector:{dv.detector_id}:{dv.git_tag}"
    if not dv.mlflow_experiment_id:
        dv.mlflow_experiment_id = await client.get_or_create_experiment(exp_name)
        await session.flush()
    run_id = await client.create_run(dv.mlflow_experiment_id)
    await client.set_run_tag(run_id, "maldet.action", body.type.value)
    await client.set_run_tag(run_id, "lolday.user", str(user.id))
    await client.set_run_tag(run_id, "lolday.detector_version", str(dv.id))

    # 9. Render resolved config (Hydra YAML)
    renderer = JobConfigRenderer(
        samples_root=settings.SAMPLES_ROOT,
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source-model",
    )
    resolved_yaml = renderer.render_config_yaml(
        stage=body.type.value,
        user_params=body.params,
        mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
        mlflow_run_id=run_id,
        mlflow_experiment_id=dv.mlflow_experiment_id,
    )
    resolved = {"yaml": resolved_yaml}

    # 10. Insert job row
    raw_token = generate_token()
    # Get detector name for image reference
    from app.models import Detector
    det = await session.get(Detector, dv.detector_id)
    det_name = det.name if det else str(dv.detector_id)

    job = Job(
        type=body.type,
        status=JobStatus.PENDING,
        detector_version_id=dv.id,
        train_dataset_id=train_ds.id if train_ds else None,
        test_dataset_id=test_ds.id if test_ds else None,
        predict_dataset_id=predict_ds.id if predict_ds else None,
        source_model_version_id=source_model.id if source_model else None,
        owner_id=user.id,
        resolved_config=resolved,
        mlflow_experiment_id=dv.mlflow_experiment_id,
        mlflow_run_id=run_id,
        idempotency_key=idem_key,
        token_hash=hash_token(raw_token),
        resource_profile=body.resource_profile,
    )
    session.add(job)
    await session.flush()

    # 11. Launch K8s Job
    secret = build_job_token_secret(job.id, raw_token)
    core_v1().create_namespaced_secret(namespace=settings.JOB_NAMESPACE, body=secret)
    manifest = build_volcano_job_manifest(
        job_id=job.id,
        job_type=body.type,
        detector_image=dv.harbor_image,
        detector_cli_command=_detector_cli(det_name),
        mlflow_experiment_id=dv.mlflow_experiment_id,
        mlflow_run_id=run_id,
        mlflow_tracking_uri=settings.MLFLOW_TRACKING_URI,
        source_run_id=source_run_id,
        source_artifact_path=(resolve_source_model_path(f"runs:/{source_run_id}/model") if source_run_id else None),
        model_name=_registered_model_name(det_name),
        resource_profile=body.resource_profile,
    )
    try:
        volcano_v1alpha1().create_namespaced_custom_object(
            group=VOLCANO_BATCH_GROUP,
            version=VOLCANO_BATCH_VERSION,
            namespace=settings.JOB_NAMESPACE,
            plural=VOLCANO_JOB_PLURAL,
            body=manifest,
        )
    except Exception:
        try:
            core_v1().delete_namespaced_secret(
                name=secret["metadata"]["name"], namespace=settings.JOB_NAMESPACE
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="failed to create K8s Job")

    job.k8s_job_name = manifest["metadata"]["name"]
    job.status = JobStatus.PREPARING
    await session.commit()
    await session.refresh(job)
    return JobRead.model_validate(job)


@router.get("", response_model=JobList)
async def list_jobs(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    type: JobType | None = None,
    status_: JobStatus | None = Query(None, alias="status"),
    detector_id: uuid.UUID | None = None,
) -> JobList:
    filters = []
    if user.role.value != "admin":
        filters.append(Job.owner_id == user.id)
    if type is not None:
        filters.append(Job.type == type)
    if status_ is not None:
        filters.append(Job.status == status_)
    if detector_id is not None:
        filters.append(
            Job.detector_version_id.in_(
                select(DetectorVersion.id).where(DetectorVersion.detector_id == detector_id)
            )
        )

    count_stmt = select(func.count()).select_from(Job)
    if filters:
        count_stmt = count_stmt.where(and_(*filters))
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = select(Job).order_by(Job.submitted_at.desc()).offset((page - 1) * page_size).limit(page_size)
    if filters:
        stmt = stmt.where(and_(*filters))
    items = (await session.execute(stmt)).scalars().all()

    return JobList(
        items=[JobSummary.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="job not found")
    return JobRead.model_validate(job)


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in NON_TERMINAL_STATUSES or job.finished_at is None:
        return _stream_live_logs(job)
    age = datetime.now(timezone.utc) - job.finished_at.replace(tzinfo=timezone.utc)
    if age.total_seconds() > 86400:
        return Response(content=job.log_tail or "", status_code=410, media_type="text/plain")
    return Response(content=job.log_tail or "", media_type="text/plain")


def _stream_live_logs(job: Job):
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={job.id}",
        )
        if not pods.items:
            return Response(content="", media_type="text/plain")
        pod = pods.items[0]
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.JOB_NAMESPACE,
            container="detector",
            tail_lines=1000,
        )
        return Response(content=log, media_type="text/plain")
    except Exception:
        BACKEND_ERRORS.labels(stage="job_logs_fetch").inc()
        logger.exception("job logs fetch failed", extra={"job_id": str(job.id)})
        return Response(content="(logs unavailable)", media_type="text/plain", status_code=503)


@router.get("/{job_id}/queue-position")
async def get_job_queue_position_endpoint(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    job = await session.get(Job, job_id)
    if job is None or (job.owner_id != user.id and user.role.value != "admin"):
        raise HTTPException(status_code=404, detail="job not found")
    position = get_job_queue_position(job.k8s_job_name) if job.k8s_job_name else None
    return {"position": position}


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> JobRead:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=403, detail="owner or admin only")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail=f"job already {job.status.value}")

    if job.k8s_job_name:
        try:
            batch_v1().delete_namespaced_job(
                name=job.k8s_job_name,
                namespace=settings.JOB_NAMESPACE,
                propagation_policy="Background",
            )
        except Exception:
            BACKEND_ERRORS.labels(stage="cancel_k8s_cleanup").inc()
            logger.exception(
                "K8s job cleanup failed on cancel",
                extra={"job_id": str(job.id), "k8s_job_name": job.k8s_job_name},
            )

    job.status = JobStatus.CANCELLED
    job.failure_reason = "cancelled_by_user" if job.owner_id == user.id else "cancelled_by_admin"
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(job)
    return JobRead.model_validate(job)
