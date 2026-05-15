"""Volcano vcjob reconciliation: status sync from K8s + stage_end events.

:func:`reconcile_job` runs once per ~10s loop iteration for every Job row
in a non-terminal state. The transition logic:

1. Read the Volcano vcjob via CustomObjectsApi (Phase 7.3+).
2. Wall-clock timeout check against ``settings.JOB_ACTIVE_DEADLINE_*``.
3. **Trust stage_end event before Volcano phase** (Phase 11b): if a
   ``stage_end`` JobEvent reports success/failure, transition immediately
   without consulting ``vjob.status.state.phase``. Detectors on a buggy
   exit path can finish their work but exit non-zero; the event is
   authoritative.
4. Otherwise dispatch on ``phase``: Completed → succeeded, Failed/Aborted
   /Terminated → failed, else update progress (PREPARING → RUNNING).

Terminal transitions schedule fire-and-forget Discord notifies (via
:func:`_fire_job_failed_notify` for failures; direct ``notify_job_completed``
call for success) and clean up the job-token secret.
"""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.job import NON_TERMINAL_STATUSES, Job, JobStatus, JobType
from app.reconciler.log_capture import _capture_job_log_tail
from app.reconciler.notify import (
    _detector_label,
    _fire_job_failed_notify,
    _primary_metric,
    _ui_url,
    _user_context,
)
from app.reconciler.projections import (
    _project_prediction_summary,
    _project_summary_metrics,
)
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)
from app.services.mlflow_client import MlflowClient
from app.services.notify import notify_job_completed

logger = logging.getLogger(__name__)


async def reconcile_job(
    session: AsyncSession, j: Job, mlflow: MlflowClient | None = None
) -> None:
    """Poll Volcano Job + MLflow state for a single job row, transition DB row.

    Phase 7.3: training jobs are ``batch.volcano.sh/v1alpha1`` Jobs (queued on
    ``lolday-training``), accessed via the generic CustomObjectsApi. Phase state
    lives at ``.status.state.phase`` (Volcano-specific enum: Pending / Running /
    Completed / Failed / Aborted / Terminated / …).

    ``mlflow`` is the app-managed MlflowClient injected from the lifespan via
    ``app.state.mlflow``. It is optional (defaults to ``None``) during the
    transition period so tests that call ``reconcile_job(session, job)`` without
    the third argument continue to work; the internal helpers each create their
    own legacy client when ``mlflow`` is ``None``.  After T13 completes, pass
    ``mlflow`` explicitly from ``reconciler_loop`` and remove the default.
    """
    if j.k8s_job_name is None:
        return

    try:
        vjob = await asyncio.to_thread(
            volcano_v1alpha1().get_namespaced_custom_object,
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
            j.finished_at = datetime.now(UTC)
            j.token_hash = (
                None  # H-20: invalidate init-container token on terminal transition
            )
            await session.commit()
            await _finalize_mlflow_run(j, "FAILED", mlflow=mlflow)
            await _fire_job_failed_notify(session, j, "k8s_job_missing")
        return

    if j.started_at is not None and _job_timed_out(j, vjob):
        try:
            await asyncio.to_thread(
                volcano_v1alpha1().delete_namespaced_custom_object,
                group=VOLCANO_BATCH_GROUP,
                version=VOLCANO_BATCH_VERSION,
                namespace=settings.JOB_NAMESPACE,
                plural=VOLCANO_JOB_PLURAL,
                name=j.k8s_job_name,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status != 404:
                BACKEND_ERRORS.labels(stage="k8s_cleanup").inc()
                logger.warning(
                    "volcano job delete returned %s for job %s",
                    exc.status,
                    j.id,
                    exc_info=True,
                )
        j.status = JobStatus.TIMEOUT
        j.failure_reason = "detector_timeout"
        j.finished_at = datetime.now(UTC)
        j.token_hash = (
            None  # H-20: invalidate init-container token on terminal transition
        )
        await session.commit()
        await _finalize_mlflow_run(j, "KILLED", mlflow=mlflow)
        await _fire_job_failed_notify(session, j, "detector_timeout")
        await _cleanup_job_secret(j)
        return

    # Phase 11b: trust stage_end event before consulting Volcano phase.
    event_status = await _check_event_terminal(session, j.id)
    if event_status == "success":
        await _handle_job_succeeded(session, j, mlflow=mlflow)
        return
    if event_status == "failure":
        await _handle_job_failed(session, j, mlflow=mlflow)
        return

    phase = (vjob.get("status") or {}).get("state", {}).get("phase", "")
    if phase == "Completed":
        await _handle_job_succeeded(session, j, mlflow=mlflow)
    elif phase in ("Failed", "Aborted", "Terminated"):
        await _handle_job_failed(session, j, mlflow=mlflow)
    else:
        await _update_job_progress(session, j)


def _job_timed_out(j: Job, vjob: dict) -> bool:
    """Check wall-clock timeout against settings.JOB_ACTIVE_DEADLINE_*.

    Only uses the DB timestamp ``j.started_at`` — vjob is accepted for signature
    symmetry with the (batch/v1) predecessor but its fields aren't consulted.

    Caller invariant: callers gate on ``j.started_at is not None`` before
    invoking this helper (single call site is in the events loop). The
    narrowing is re-asserted here so the static checker can prove it.
    """
    if j.started_at is None:
        raise RuntimeError(
            f"caller invariant violated: _job_timed_out called for job {j.id} with no started_at"
        )
    deadline_map = {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }
    deadline = deadline_map.get(j.type, 3600)
    elapsed = (datetime.now(UTC) - j.started_at.replace(tzinfo=UTC)).total_seconds()
    return elapsed > deadline + 60


async def _check_event_terminal(session: AsyncSession, job_id: uuid.UUID) -> str | None:
    """Return 'success' / 'failure' based on the most recent stage_end event, else None."""
    from app.models import JobEvent

    stmt = (
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.kind == "stage_end")
        .order_by(JobEvent.ts.desc())
        .limit(1)
    )
    row = (await session.scalars(stmt)).first()
    if row is None:
        return None
    status = (row.payload or {}).get("status")
    return status if status in ("success", "failure") else None


async def _update_job_progress(session: AsyncSession, j: Job) -> None:
    """Transition PREPARING → RUNNING once the detector container starts."""
    try:
        pods = await asyncio.to_thread(
            core_v1().list_namespaced_pod,
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
            j.started_at = datetime.now(UTC)
        await session.commit()


async def _handle_job_succeeded(
    session: AsyncSession, j: Job, *, mlflow: MlflowClient | None = None
) -> None:
    # Phase 11e: summary_metrics is no longer sourced from MLflow — the
    # `_project_summary_metrics` projection below reads from the canonical
    # job_events stream. We still need an MlflowClient for the downstream
    # model-registration call.
    #
    # In production ``mlflow`` is always the lifespan-owned client (passed from
    # reconciler_loop → reconcile_job → here). The ``None`` fallback only fires
    # in tests that call reconcile_job without the third argument; it creates a
    # short-lived httpx.AsyncClient that is closed at function exit.
    client = (
        mlflow
        if mlflow is not None
        else MlflowClient(
            settings.MLFLOW_TRACKING_URI,
            http_client=httpx.AsyncClient(timeout=httpx.Timeout(10.0)),
        )
    )

    log_tail = await _capture_job_log_tail(j)

    j.log_tail = log_tail
    j.status = JobStatus.SUCCEEDED
    j.finished_at = datetime.now(UTC)
    j.token_hash = None  # H-20: invalidate init-container token on terminal transition

    if j.type == JobType.TRAIN:
        try:
            await _register_model_from_job(session, client, j)
        except Exception:
            BACKEND_ERRORS.labels(stage="model_registration").inc()
            logger.exception("model registration failed for job %s", j.id)

    # Commit the terminal status before projecting events. Projection failure
    # must not block job termination — it's an opportunistic read-model
    # refresh, not part of the state machine transition.
    await session.commit()
    # Idempotent MLflow finalize — maldet typically already wrote FINISHED,
    # this is the controller's safety net.
    await _finalize_mlflow_run(j, "FINISHED", mlflow=client)

    try:
        await _project_summary_metrics(session, j.id)
    except Exception:
        BACKEND_ERRORS.labels(stage="summary_projection").inc()
        logger.exception(
            "summary_metrics projection failed", extra={"job_id": str(j.id)}
        )

    if j.type == JobType.PREDICT:
        try:
            await _project_prediction_summary(session, j)
        except Exception:
            BACKEND_ERRORS.labels(stage="prediction_summary_projection").inc()
            logger.exception(
                "prediction_summary projection failed",
                extra={"job_id": str(j.id)},
            )

    # Re-read the projection result so the notify carries the same primary
    # metric the user will see on /jobs/<id>.
    metrics_for_notify = (j.summary_metrics or {}).get("metrics", {}) or {}

    # Notify user of completion (skipped for CF Access service tokens —
    # see _user_context).
    ctx = await _user_context(session, j.owner_id)
    if ctx is not None:
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
            sa = (
                j.started_at
                if j.started_at.tzinfo
                else j.started_at.replace(tzinfo=UTC)
            )
            fa = (
                j.finished_at
                if j.finished_at.tzinfo
                else j.finished_at.replace(tzinfo=UTC)
            )
            duration = int((fa - sa).total_seconds())
        mlflow_url = (
            _ui_url(f"/runs/{j.mlflow_experiment_id}/{j.mlflow_run_id}")
            if j.mlflow_experiment_id and j.mlflow_run_id
            else None
        )
        asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
            notify_job_completed(
                user_name=ctx.name,
                user_discord_id=ctx.discord_id,
                job_type=j.type.value,
                detector_label=detector_label,
                dataset_name=dataset_name,
                duration_seconds=duration,
                primary_metric=_primary_metric(metrics_for_notify),
                job_url=_ui_url(f"/jobs/{j.id}"),
                mlflow_url=mlflow_url,
            )
        )
    await _cleanup_job_secret(j)


async def _register_model_from_job(
    session: AsyncSession, client: MlflowClient, j: Job
) -> None:
    from app.models import (
        Detector,
        DetectorVersion,
        ModelVersion,
        RegisteredModel,
        User,
    )
    from app.models.model_registry import (
        ModelVersionStage,
        ModelVersionVisibility,
    )

    if j.mlflow_run_id is None:
        raise RuntimeError(
            f"job {j.id} reached model registration without mlflow_run_id "
            "(TRAIN jobs must populate it during stage_start handling)"
        )
    dv = await session.get(DetectorVersion, j.detector_version_id)
    if dv is None:
        raise RuntimeError(
            f"FK invariant violated: job {j.id} references missing DetectorVersion {j.detector_version_id}"
        )
    det = await session.get(Detector, dv.detector_id)
    if det is None:
        raise RuntimeError(
            f"FK invariant violated: DetectorVersion {dv.id} references missing Detector {dv.detector_id}"
        )
    owner = await session.get(User, j.owner_id)
    if owner is None:
        raise RuntimeError(
            f"FK invariant violated: job {j.id} references missing User {j.owner_id}"
        )

    # Upsert RegisteredModel for (owner, detector). One per user-detector pair.
    rm = (
        await session.execute(
            select(RegisteredModel).where(
                RegisteredModel.owner_id == owner.id,
                RegisteredModel.detector_id == det.id,
            )
        )
    ).scalar_one_or_none()
    if rm is None:
        rm = RegisteredModel(owner_id=owner.id, detector_id=det.id)
        session.add(rm)
        await session.flush()

    mlflow_name = f"{owner.handle}/{det.name}"  # HF-style namespace
    await client.create_registered_model(mlflow_name)  # idempotent in MLflow
    mv_resp = await client.create_model_version(
        name=mlflow_name,
        source=f"runs:/{j.mlflow_run_id}/model",
        run_id=j.mlflow_run_id,
    )

    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=int(mv_resp["version"]),
        mlflow_run_id=j.mlflow_run_id,
        current_stage=ModelVersionStage.NONE,
        visibility=ModelVersionVisibility.PRIVATE,
        detector_version_id=j.detector_version_id,
        source_job_id=j.id,
        owner_id=j.owner_id,
    )
    session.add(mv)


async def _handle_job_failed(
    session: AsyncSession, j: Job, *, mlflow: MlflowClient | None = None
) -> None:
    reason = await _extract_job_failure_reason(j)
    log_tail = await _capture_job_log_tail(j)
    j.status = JobStatus.FAILED
    j.failure_reason = reason
    j.log_tail = log_tail
    j.finished_at = datetime.now(UTC)
    j.token_hash = None  # H-20: invalidate init-container token on terminal transition
    await session.commit()
    await _finalize_mlflow_run(j, "FAILED", mlflow=mlflow)

    # Phase 11e: failed jobs may still have meaningful early-stage metrics
    # (e.g. an evaluator that produced a result before the process exited).
    # Project them too so the UI summary card surfaces what's available.
    try:
        await _project_summary_metrics(session, j.id)
    except Exception:
        BACKEND_ERRORS.labels(stage="summary_projection").inc()
        logger.exception(
            "summary_metrics projection failed", extra={"job_id": str(j.id)}
        )

    await _fire_job_failed_notify(session, j, reason)
    await _cleanup_job_secret(j)


async def _extract_job_failure_reason(j: Job) -> str:
    try:
        pods = await asyncio.to_thread(
            core_v1().list_namespaced_pod,
            namespace=settings.JOB_NAMESPACE,
            label_selector=f"lolday.job-id={j.id}",
        )
    except ApiException:
        return "k8s_api_error"
    if not pods.items:
        return "pod_missing"
    pod = pods.items[0]

    for ic in pod.status.init_container_statuses or []:
        if (
            ic.state
            and ic.state.terminated
            and ic.state.terminated.exit_code not in (0, None)
        ):
            if ic.name == "model-fetcher":
                return "source_model_not_found"
            return f"init_{ic.name}_failed"

    for cs in pod.status.container_statuses or []:
        if cs.state and cs.state.terminated:
            ec = cs.state.terminated.exit_code
            if ec == 137:
                return "detector_oom"
            if ec not in (0, None):
                return "detector_exit_nonzero"
    return "unknown_failure"


async def _finalize_mlflow_run(
    j: Job,
    status: str,
    *,
    end_time_ms: int | None = None,
    mlflow: MlflowClient | None = None,
) -> None:
    """Update the MLflow run to a terminal status when lolday terminates the Job.

    Idempotent: maldet typically writes ``FINISHED`` itself on success, so a
    second update is a no-op overwrite from MLflow's side. Critical for
    ``FAILED`` / ``KILLED`` cases where the pod died before maldet could
    write ``end_run()``.

    Best-effort: a flaky MLflow server must NOT block the DB-side state
    machine transition.  Spec § 5.5.
    """
    if not j.mlflow_run_id:
        return
    # In production ``mlflow`` is always the lifespan-owned client. The ``None``
    # fallback creates a short-lived client for backward-compat test call sites
    # that call _handle_job_failed / reconcile_job without the mlflow arg.
    client = (
        mlflow
        if mlflow is not None
        else MlflowClient(
            settings.MLFLOW_TRACKING_URI,
            http_client=httpx.AsyncClient(timeout=httpx.Timeout(10.0)),
        )
    )
    try:
        await client.update_run(
            j.mlflow_run_id,
            status=status,
            end_time_ms=end_time_ms or int(time.time() * 1000),
        )
    except Exception as exc:
        logger.warning("mlflow finalize failed for job %s: %s", j.id, exc)
        BACKEND_ERRORS.labels(stage="mlflow_finalize").inc()


async def _cleanup_job_secret(j: Job) -> None:
    try:
        from app.services.job_spec import _job_token_secret_name

        await asyncio.to_thread(
            core_v1().delete_namespaced_secret,
            name=_job_token_secret_name(j.id),
            namespace=settings.JOB_NAMESPACE,
        )
    except ApiException as exc:
        if exc.status != 404:
            BACKEND_ERRORS.labels(stage="k8s_cleanup").inc()
            logger.warning(
                "job token secret cleanup returned %s for job %s",
                exc.status,
                j.id,
                exc_info=True,
            )


# Re-export NON_TERMINAL_STATUSES for any consumer that imports from this module.
__all__ = [
    "NON_TERMINAL_STATUSES",
    "_check_event_terminal",
    "_cleanup_job_secret",
    "_extract_job_failure_reason",
    "_handle_job_failed",
    "_handle_job_succeeded",
    "_job_timed_out",
    "_register_model_from_job",
    "_update_job_progress",
    "reconcile_job",
]
