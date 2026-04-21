import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.metrics import BACKEND_ERRORS
from app.models.detector import (
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanResult, ScanStatus
from app.services.notify import (
    notify_build_completed,
    notify_build_failed,
    notify_job_completed,
    notify_job_failed,
    notify_trivy_blocked,
)
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    batch_v1,
    core_v1,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

IN_FLIGHT = {
    DetectorBuildStatus.PENDING,
    DetectorBuildStatus.CLONING,
    DetectorBuildStatus.VALIDATING,
    DetectorBuildStatus.BUILDING,
    DetectorBuildStatus.SCANNING,
}


# ---- notify helpers ----------------------------------------------------------


async def _user_context(session: AsyncSession, user_id) -> tuple[str, str | None]:
    """Returns (name, discord_user_id).

    Name falls back through display_name → email local-part → literal "user"
    (the last case only triggers when the user row is missing entirely,
    since email is required on User).
    """
    from app.models import User
    user = await session.get(User, user_id)
    if user is None:
        return ("unknown", None)
    name = user.display_name or (user.email.split("@")[0] if user.email else "user")
    return (name, user.discord_user_id)


async def _detector_label(session: AsyncSession, detector_id) -> str:
    """Returns detector.display_name, or "unknown" if the row was deleted."""
    from app.models import Detector
    det = await session.get(Detector, detector_id)
    if det is None:
        return "unknown"
    return det.display_name


def _ui_url(path: str) -> str:
    """Absolute UI link built from `settings.LOLDAY_UI_BASE_URL`."""
    return f"{settings.LOLDAY_UI_BASE_URL.rstrip('/')}{path}"


def _primary_metric(metrics: dict) -> tuple[str, float] | None:
    """Returns the first available metric in priority order f1 > accuracy >
    precision > recall; None if none are numeric."""
    for key in ("f1", "accuracy", "precision", "recall"):
        val = metrics.get(key)
        if isinstance(val, (int, float)):
            return (key, float(val))
    return None


async def _fire_job_failed_notify(
    session: AsyncSession, j, reason: str,
) -> None:
    """Schedule a job-failed Discord notify without blocking the reconciler.

    Shared helper for the 3 terminal-failure paths: Volcano Failed/Aborted
    phase, wall-clock TIMEOUT, and k8s_job_missing (404 on GET).
    """
    from app.models import DatasetConfig, DetectorVersion
    user_name, discord_id = await _user_context(session, j.owner_id)
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det_label = await _detector_label(session, dv.detector_id) if dv else "unknown"
    detector_label = f"{det_label} {dv.git_tag}" if dv else det_label
    dataset_name = None
    ds_id = j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id
    if ds_id:
        ds = await session.get(DatasetConfig, ds_id)
        dataset_name = ds.name if ds else None
    asyncio.create_task(notify_job_failed(
        user_name=user_name,
        user_discord_id=discord_id,
        job_type=j.type.value,
        detector_label=detector_label,
        dataset_name=dataset_name,
        failure_reason=reason,
        job_url=_ui_url(f"/jobs/{j.id}"),
    ))

# Loop tuning. Module-level so tests can monkeypatch to collapse iteration time.
SYNC_EVERY_N_ITERATIONS = 6
RECONCILER_WAIT_SECONDS = 10


async def reconcile_build(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        job = batch_v1().read_namespaced_job(
            name=b.k8s_job_name, namespace=settings.BUILD_NAMESPACE
        )
    except ApiException as e:
        if e.status == 404:
            b.status = DetectorBuildStatus.FAILED
            b.failure_reason = "k8s_job_missing"
            b.finished_at = datetime.now(timezone.utc)
            await session.commit()
            user_name, discord_id = await _user_context(session, b.triggered_by_id)
            label = await _detector_label(session, b.detector_id)
            asyncio.create_task(notify_build_failed(
                user_name=user_name,
                user_discord_id=discord_id,
                detector_label=label,
                git_tag=b.git_tag,
                failure_reason="k8s_job_missing",
                build_url=_ui_url(f"/detectors/{b.detector_id}"),
            ))
        return

    if job.status.succeeded:
        await _handle_succeeded(session, b)
    elif job.status.failed:
        await _handle_failed(session, b, job)
    elif (datetime.now(timezone.utc) - b.started_at.replace(tzinfo=timezone.utc)).total_seconds() \
            > settings.BUILD_TIMEOUT_SECONDS + 60:
        await _handle_timeout(session, b)
    else:
        await _update_progress(session, b, job)


async def _handle_succeeded(session: AsyncSession, b: DetectorBuild) -> None:
    from app.models.detector import Detector
    detector = await session.get(Detector, b.detector_id)
    harbor = HarborClient(
        settings.HARBOR_URL, settings.HARBOR_ADMIN_USERNAME, settings.HARBOR_ADMIN_PASSWORD
    )
    digest = await harbor.get_artifact_digest("detectors", detector.name, b.git_tag)
    if digest is None:
        b.status = DetectorBuildStatus.FAILED
        b.failure_reason = "artifact_missing_in_harbor"
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return

    scan = await harbor.get_scan("detectors", detector.name, digest)
    if scan.status in {ScanStatus.PENDING, ScanStatus.RUNNING, ScanStatus.NOT_SCANNED}:
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return

    if scan.critical > 0:
        await harbor.delete_artifact("detectors", detector.name, digest)
        b.status = DetectorBuildStatus.CVE_BLOCKED
        b.failure_reason = f"cve_blocked: critical={scan.critical} high={scan.high}"
        b.trivy_critical = scan.critical
        b.trivy_high = scan.high
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
        user_name, discord_id = await _user_context(session, b.triggered_by_id)
        label = await _detector_label(session, b.detector_id)
        asyncio.create_task(notify_trivy_blocked(
            user_name=user_name,
            user_discord_id=discord_id,
            detector_label=label,
            git_tag=b.git_tag,
            cve_summary=f"{scan.critical} critical, {scan.high} high",
            build_url=_ui_url(f"/detectors/{b.detector_id}"),
        ))
    else:
        # record version
        version = DetectorVersion(
            detector_id=b.detector_id,
            git_tag=b.git_tag,
            git_sha=await _read_git_sha_from_log(b),
            harbor_image=f"{settings.HARBOR_IMAGE_PREFIX}/detectors/{detector.name}:{b.git_tag}",
            image_digest=digest,
            config_schema=b.pending_schema or {},
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(version)
        b.status = DetectorBuildStatus.SUCCEEDED
        b.git_sha = version.git_sha
        b.trivy_critical = scan.critical
        b.trivy_high = scan.high
        b.finished_at = datetime.now(timezone.utc)
        await session.commit()
        user_name, discord_id = await _user_context(session, b.triggered_by_id)
        label = await _detector_label(session, b.detector_id)
        asyncio.create_task(notify_build_completed(
            user_name=user_name,
            user_discord_id=discord_id,
            detector_label=label,
            git_tag=b.git_tag,
            commit_sha=version.git_sha or "",
            build_url=_ui_url(f"/detectors/{b.detector_id}"),
        ))
    await _cleanup_build_secret(b.id)


async def _handle_failed(session: AsyncSession, b: DetectorBuild, job) -> None:
    reason = await _extract_failure_reason(b)
    b.status = DetectorBuildStatus.FAILED
    b.failure_reason = reason
    b.log_tail = await _capture_log_tail(b)
    b.finished_at = datetime.now(timezone.utc)
    await session.commit()
    user_name, discord_id = await _user_context(session, b.triggered_by_id)
    label = await _detector_label(session, b.detector_id)
    asyncio.create_task(notify_build_failed(
        user_name=user_name,
        user_discord_id=discord_id,
        detector_label=label,
        git_tag=b.git_tag,
        failure_reason=reason,
        build_url=_ui_url(f"/detectors/{b.detector_id}"),
    ))
    await _cleanup_build_secret(b.id)


async def _handle_timeout(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        batch_v1().delete_namespaced_job(
            name=b.k8s_job_name,
            namespace=settings.BUILD_NAMESPACE,
            propagation_policy="Background",
        )
    except ApiException:
        pass
    b.status = DetectorBuildStatus.TIMEOUT
    b.failure_reason = "build exceeded timeout"
    b.finished_at = datetime.now(timezone.utc)
    await session.commit()
    user_name, discord_id = await _user_context(session, b.triggered_by_id)
    label = await _detector_label(session, b.detector_id)
    asyncio.create_task(notify_build_failed(
        user_name=user_name,
        user_discord_id=discord_id,
        detector_label=label,
        git_tag=b.git_tag,
        failure_reason="build exceeded timeout",
        build_url=_ui_url(f"/detectors/{b.detector_id}"),
    ))
    await _cleanup_build_secret(b.id)


async def _update_progress(session: AsyncSession, b: DetectorBuild, job) -> None:
    """Update status based on which init container is running."""
    pods = core_v1().list_namespaced_pod(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
    )
    if not pods.items:
        return
    pod = pods.items[0]
    init_statuses = pod.status.init_container_statuses or []
    finished = {ic.name for ic in init_statuses if ic.state.terminated}
    if "validate" in finished:
        b.status = DetectorBuildStatus.BUILDING
    elif "clone" in finished:
        b.status = DetectorBuildStatus.VALIDATING
    else:
        b.status = DetectorBuildStatus.CLONING
    await session.commit()


async def _capture_log_tail(b: DetectorBuild) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.BUILD_NAMESPACE,
            label_selector=f"lolday.io/build-id={b.id}",
        )
        if not pods.items:
            return ""
        pod = pods.items[0]
        # Combine kaniko logs (main container) if available
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.BUILD_NAMESPACE,
            container="kaniko",
            tail_lines=200,
        )
        return log[-settings.BUILD_LOG_TAIL_BYTES:]
    except ApiException:
        return ""


async def _extract_failure_reason(b: DetectorBuild) -> str:
    """Examine pod's init containers to determine which step failed."""
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.BUILD_NAMESPACE,
            label_selector=f"lolday.io/build-id={b.id}",
        )
        if not pods.items:
            return "pod_missing"
        pod = pods.items[0]
        for ic in (pod.status.init_container_statuses or []):
            if ic.state.terminated and ic.state.terminated.exit_code != 0:
                return f"{ic.name}_failed: exit={ic.state.terminated.exit_code}"
        for cs in (pod.status.container_statuses or []):
            if cs.state.terminated and cs.state.terminated.exit_code != 0:
                return f"{cs.name}_failed: exit={cs.state.terminated.exit_code}"
        return "unknown_failure"
    except ApiException:
        return "k8s_api_error"


async def _read_git_sha_from_log(b: DetectorBuild) -> str:
    """git_sha is populated on build row by the validate container's schema callback
    (see Task 10 /internal/builds/{id}/schema — payload includes git_sha)."""
    return b.git_sha or ""


async def _cleanup_build_secret(build_id) -> None:
    try:
        core_v1().delete_namespaced_secret(
            name=build_secret_name(build_id),
            namespace=settings.BUILD_NAMESPACE,
        )
    except ApiException:
        pass


async def reconciler_loop(stop_event: asyncio.Event) -> None:
    logger.info("reconciler started (build + job)")
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        try:
            async with async_session_maker() as session:
                # Build reconcile pass
                res_builds = await session.execute(
                    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
                )
                for b in res_builds.scalars().all():
                    try:
                        await reconcile_build(session, b)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_build").inc()
                        logger.exception("reconcile_build failed", extra={"build_id": str(b.id)})

                # Job reconcile pass (Phase 4)
                res_jobs = await session.execute(
                    select(Job).where(Job.status.in_(NON_TERMINAL_STATUSES))
                )
                for j in res_jobs.scalars().all():
                    try:
                        await reconcile_job(session, j)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_job").inc()
                        logger.exception("reconcile_job failed", extra={"job_id": str(j.id)})

                # Model version sync every N iterations (~60s at default N=6)
                if iteration % SYNC_EVERY_N_ITERATIONS == 0:
                    try:
                        await sync_model_versions(session)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="sync_model_versions").inc()
                        logger.exception("sync_model_versions failed")
        except Exception:
            BACKEND_ERRORS.labels(stage="reconciler_iteration").inc()
            logger.exception("reconciler iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RECONCILER_WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
    logger.info("reconciler stopped")


# =============================================================================
# Phase 4: Job + Model Registry reconciliation
# =============================================================================

from app.models.job import Job, JobStatus, JobType, NON_TERMINAL_STATUSES  # noqa: E402
from app.services.mlflow_client import MlflowClient  # noqa: E402


async def reconcile_job(session: AsyncSession, j: Job) -> None:
    """Poll Volcano Job + MLflow state for a single job row, transition DB row.

    Phase 7.3: training jobs are ``batch.volcano.sh/v1alpha1`` Jobs (queued on
    ``lolday-training``), accessed via the generic CustomObjectsApi. Phase state
    lives at ``.status.state.phase`` (Volcano-specific enum: Pending / Running /
    Completed / Failed / Aborted / Terminated / …).
    """
    if j.k8s_job_name is None:
        return

    try:
        vjob = volcano_v1alpha1().get_namespaced_custom_object(
            group=VOLCANO_BATCH_GROUP,
            version=VOLCANO_BATCH_VERSION,
            namespace=settings.JOB_NAMESPACE,
            plural=VOLCANO_JOB_PLURAL,
            name=j.k8s_job_name,
        )
    except ApiException as e:
        if e.status == 404:
            j.status = JobStatus.FAILED
            j.failure_reason = "k8s_job_missing"
            j.finished_at = datetime.now(timezone.utc)
            await session.commit()
            await _fire_job_failed_notify(session, j, "k8s_job_missing")
        return

    if j.started_at is not None and _job_timed_out(j, vjob):
        try:
            volcano_v1alpha1().delete_namespaced_custom_object(
                group=VOLCANO_BATCH_GROUP,
                version=VOLCANO_BATCH_VERSION,
                namespace=settings.JOB_NAMESPACE,
                plural=VOLCANO_JOB_PLURAL,
                name=j.k8s_job_name,
                propagation_policy="Background",
            )
        except ApiException:
            pass
        j.status = JobStatus.TIMEOUT
        j.failure_reason = "detector_timeout"
        j.finished_at = datetime.now(timezone.utc)
        await session.commit()
        await _fire_job_failed_notify(session, j, "detector_timeout")
        await _cleanup_job_secret(j)
        return

    phase = (vjob.get("status") or {}).get("state", {}).get("phase", "")
    if phase == "Completed":
        await _handle_job_succeeded(session, j)
    elif phase in ("Failed", "Aborted", "Terminated"):
        await _handle_job_failed(session, j)
    else:
        await _update_job_progress(session, j)


def _job_timed_out(j: Job, vjob: dict) -> bool:
    """Check wall-clock timeout against settings.JOB_ACTIVE_DEADLINE_*.

    Only uses the DB timestamp ``j.started_at`` — vjob is accepted for signature
    symmetry with the (batch/v1) predecessor but its fields aren't consulted.
    """
    deadline_map = {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }
    deadline = deadline_map.get(j.type, 3600)
    elapsed = (datetime.now(timezone.utc) - j.started_at.replace(tzinfo=timezone.utc)).total_seconds()
    return elapsed > deadline + 60


async def _update_job_progress(session: AsyncSession, j: Job) -> None:
    """Transition PREPARING → RUNNING once the detector container starts."""
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
    except ApiException:
        return
    if not pods.items:
        return
    pod = pods.items[0]
    if pod.status.phase == "Running" and j.status != JobStatus.RUNNING:
        j.status = JobStatus.RUNNING
        if j.started_at is None:
            j.started_at = datetime.now(timezone.utc)
        await session.commit()


async def _handle_job_succeeded(session: AsyncSession, j: Job) -> None:
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    run = await client.get_run(j.mlflow_run_id)
    metrics_raw = run["data"].get("metrics", {})
    if isinstance(metrics_raw, list):
        metrics = {m["key"]: m["value"] for m in metrics_raw}
    else:
        metrics = dict(metrics_raw)

    log_tail = await _capture_job_log_tail(j)

    j.summary_metrics = metrics
    j.log_tail = log_tail
    j.status = JobStatus.SUCCEEDED
    j.finished_at = datetime.now(timezone.utc)

    if j.type == JobType.TRAIN:
        try:
            await _register_model_from_job(session, client, j)
        except Exception:
            BACKEND_ERRORS.labels(stage="model_registration").inc()
            logger.exception("model registration failed for job %s", j.id)

    await session.commit()

    # Notify user of completion.
    user_name, discord_id = await _user_context(session, j.owner_id)
    from app.models import DetectorVersion
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det_label = await _detector_label(session, dv.detector_id) if dv else "unknown"
    detector_label = f"{det_label} {dv.git_tag}" if dv else det_label
    dataset_name = None
    if j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id:
        from app.models import DatasetConfig
        ds_id = j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id
        ds = await session.get(DatasetConfig, ds_id)
        dataset_name = ds.name if ds else None
    duration = None
    if j.started_at and j.finished_at:
        sa = j.started_at if j.started_at.tzinfo else j.started_at.replace(tzinfo=timezone.utc)
        fa = j.finished_at if j.finished_at.tzinfo else j.finished_at.replace(tzinfo=timezone.utc)
        duration = int((fa - sa).total_seconds())
    mlflow_url = (
        _ui_url(f"/runs/{j.mlflow_experiment_id}/{j.mlflow_run_id}")
        if j.mlflow_experiment_id and j.mlflow_run_id else None
    )
    asyncio.create_task(notify_job_completed(
        user_name=user_name,
        user_discord_id=discord_id,
        job_type=j.type.value,
        detector_label=detector_label,
        dataset_name=dataset_name,
        duration_seconds=duration,
        primary_metric=_primary_metric(metrics),
        job_url=_ui_url(f"/jobs/{j.id}"),
        mlflow_url=mlflow_url,
    ))
    await _cleanup_job_secret(j)


async def _register_model_from_job(
    session: AsyncSession, client: MlflowClient, j: Job
) -> None:
    from app.models import Detector, DetectorVersion, ModelVersion
    from app.models.model_registry import ModelVersionStage

    dv = await session.get(DetectorVersion, j.detector_version_id)
    det = await session.get(Detector, dv.detector_id)
    name = det.name

    await client.create_registered_model(name)
    mv_resp = await client.create_model_version(
        name=name, source=f"runs:/{j.mlflow_run_id}/model", run_id=j.mlflow_run_id
    )
    mlflow_version = int(mv_resp["version"])

    mv = ModelVersion(
        mlflow_name=name,
        mlflow_version=mlflow_version,
        mlflow_run_id=j.mlflow_run_id,
        current_stage=ModelVersionStage.NONE,
        detector_version_id=j.detector_version_id,
        source_job_id=j.id,
        owner_id=j.owner_id,
    )
    session.add(mv)


async def _handle_job_failed(session: AsyncSession, j: Job) -> None:
    reason = await _extract_job_failure_reason(j)
    log_tail = await _capture_job_log_tail(j)
    j.status = JobStatus.FAILED
    j.failure_reason = reason
    j.log_tail = log_tail
    j.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await _fire_job_failed_notify(session, j, reason)
    await _cleanup_job_secret(j)


async def _extract_job_failure_reason(j: Job) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
    except ApiException:
        return "k8s_api_error"
    if not pods.items:
        return "pod_missing"
    pod = pods.items[0]

    for ic in (pod.status.init_container_statuses or []):
        if ic.state and ic.state.terminated and ic.state.terminated.exit_code not in (0, None):
            if ic.name == "model-fetcher":
                return "source_model_not_found"
            return f"init_{ic.name}_failed"

    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated:
            ec = cs.state.terminated.exit_code
            if ec == 137:
                return "detector_oom"
            if ec not in (0, None):
                return "detector_exit_nonzero"
    return "unknown_failure"


async def _capture_job_log_tail(j: Job) -> str:
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
        if not pods.items:
            return ""
        pod = pods.items[0]
        log = core_v1().read_namespaced_pod_log(
            name=pod.metadata.name,
            namespace=settings.JOB_NAMESPACE,
            container="detector",
            tail_lines=200,
        )
        return log[-8192:]
    except ApiException:
        return ""


async def _cleanup_job_secret(j: Job) -> None:
    try:
        from app.services.job_spec import _job_token_secret_name
        core_v1().delete_namespaced_secret(
            name=_job_token_secret_name(j.id),
            namespace=settings.JOB_NAMESPACE,
        )
    except ApiException:
        pass


async def sync_model_versions(session: AsyncSession) -> None:
    """Pull latest stages from MLflow; reflect transitions initiated outside lolday."""
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    from app.models import ModelVersion
    from app.models.model_registry import ModelVersionStage

    all_local = (await session.execute(select(ModelVersion))).scalars().all()
    if not all_local:
        return

    remote = await client.search_model_versions()
    by_key = {(m["name"], int(m["version"])): m for m in remote}

    for mv in all_local:
        rem = by_key.get((mv.mlflow_name, mv.mlflow_version))
        if rem is None:
            continue
        remote_stage = rem.get("current_stage", "None")
        try:
            stage_enum = ModelVersionStage(remote_stage)
        except ValueError:
            continue
        if stage_enum != mv.current_stage:
            mv.current_stage = stage_enum
            mv.last_transitioned_at = datetime.now(timezone.utc)
    await session.commit()
