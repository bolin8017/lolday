"""Build reconciliation: orchestrator + Job-state handlers.

:func:`reconcile_build` runs once per loop iteration for every DetectorBuild
in an in-flight status. The flow:

1. Read the K8s Job (``batch/v1`` Job, not Volcano — builds run on the
   build namespace's BuildKit pod).
2. Wall-clock timeout check against ``settings.BUILD_TIMEOUT_SECONDS + 60``.
3. Dispatch on K8s Job status: Succeeded → :func:`_handle_succeeded` (which
   delegates the post-scan finalization to
   :func:`app.reconciler.build_finalize._finalize_clean_scan`),
   Failed → :func:`_handle_failed`, otherwise → :func:`_update_progress`.

Helpers (``_extract_failure_reason``, ``_cleanup_build_secret``) are shared
across the three terminal paths.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from kubernetes.client import ApiException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.detector import DetectorBuild, DetectorBuildStatus
from app.reconciler.build_finalize import _finalize_clean_scan
from app.reconciler.log_capture import _capture_log_tail
from app.reconciler.notify import _detector_label, _ui_url, _user_context
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanStatus
from app.services.k8s import batch_v1, core_v1
from app.services.notify import notify_build_failed

logger = logging.getLogger(__name__)

IN_FLIGHT = {
    DetectorBuildStatus.PENDING,
    DetectorBuildStatus.CLONING,
    DetectorBuildStatus.VALIDATING,
    DetectorBuildStatus.BUILDING,
    DetectorBuildStatus.SCANNING,
}


async def reconcile_build(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        job = batch_v1().read_namespaced_job(
            name=b.k8s_job_name, namespace=settings.BUILD_NAMESPACE
        )
    except ApiException as e:
        if e.status == 404:
            b.status = DetectorBuildStatus.FAILED
            b.failure_reason = "k8s_job_missing"
            b.finished_at = datetime.now(UTC)
            await session.commit()
            ctx = await _user_context(session, b.triggered_by_id)
            if ctx is not None:
                label = await _detector_label(session, b.detector_id)
                asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
                    notify_build_failed(
                        user_name=ctx.name,
                        user_discord_id=ctx.discord_id,
                        detector_label=label,
                        git_tag=b.git_tag,
                        failure_reason="k8s_job_missing",
                        build_url=_ui_url(f"/detectors/{b.detector_id}"),
                    )
                )
        return

    # Wall-clock timeout gates every post-build state. A build whose Job
    # succeeded but whose scan is stuck (Harbor Trivy persistently Error)
    # would otherwise route to _handle_succeeded forever; this check has
    # to sit above the dispatch, not inside an elif, for the Error-retry
    # loop to be genuinely bounded.
    if (
        datetime.now(UTC) - b.started_at.replace(tzinfo=UTC)
    ).total_seconds() > settings.BUILD_TIMEOUT_SECONDS + 60:
        await _handle_timeout(session, b)
        return

    if job.status.succeeded:
        await _handle_succeeded(session, b)
    elif job.status.failed:
        await _handle_failed(session, b, job)
    else:
        await _update_progress(session, b, job)


async def _handle_succeeded(session: AsyncSession, b: DetectorBuild) -> None:
    """Orchestrator for builds whose K8s Job succeeded. Fetches the artifact
    digest and Harbor scan status, then either short-circuits (scan not yet
    done) or hands off to :func:`_finalize_clean_scan` for the SUCCESS path."""
    from app.models.detector import Detector

    detector = await session.get(Detector, b.detector_id)
    harbor = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )
    digest = await harbor.get_artifact_digest("detectors", detector.name, b.git_tag)
    if digest is None:
        b.status = DetectorBuildStatus.FAILED
        b.failure_reason = "artifact_missing_in_harbor"
        b.finished_at = datetime.now(UTC)
        await session.commit()
        return

    scan = await harbor.get_scan("detectors", detector.name, digest)
    if scan.status in {ScanStatus.NOT_SCANNED, ScanStatus.ERROR}:
        # ERROR means a prior scan terminally failed (most often: transient
        # Trivy DB cache-lock timeout). Must NEVER promote — critical=0 in
        # that case is "we didn't learn anything," not "clean." The caller's
        # wall-clock check at reconcile_build bounds the retry loop.
        if scan.status == ScanStatus.ERROR:
            BACKEND_ERRORS.labels(stage="harbor_scan_error_retry").inc()
            logger.warning(
                "Harbor returned scan_status=Error for build=%s detector=%s digest=%s "
                "— retriggering scan (not promoting)",
                b.id,
                detector.name,
                digest,
            )
        try:
            await harbor.trigger_scan("detectors", detector.name, digest)
        except httpx.HTTPError as e:
            BACKEND_ERRORS.labels(stage="harbor_trigger_scan").inc()
            logger.warning(
                "trigger_scan failed for build=%s detector=%s digest=%s: %s "
                "(will retry next reconcile cycle)",
                b.id,
                detector.name,
                digest,
                e,
            )
            # Do NOT flip to SCANNING — leave the build in its current status
            # so the next loop pass re-enters this branch and retries.
            return
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return
    if scan.status in {ScanStatus.PENDING, ScanStatus.RUNNING}:
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return
    if scan.status != ScanStatus.SUCCESS:
        # Defensive: an unknown ScanStatus (future Harbor value) must never
        # fall through to promotion. Keep the build SCANNING until timeout
        # or an operator intervenes.
        BACKEND_ERRORS.labels(stage="harbor_scan_unhandled_status").inc()
        logger.error(
            "unhandled Harbor scan status %s for build=%s detector=%s digest=%s",
            scan.status,
            b.id,
            detector.name,
            digest,
        )
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return

    # Scan SUCCESS — finalize (CVE block or promote)
    await _finalize_clean_scan(session, b, harbor, detector, digest, scan)


async def _handle_failed(session: AsyncSession, b: DetectorBuild, job) -> None:
    reason = await _extract_failure_reason(b)
    b.status = DetectorBuildStatus.FAILED
    b.failure_reason = reason
    # Phase 13a follow-up (PR review EH-1): protect log capture so a
    # K8s-side blip doesn't keep the build oscillating; symmetric with
    # _handle_succeeded.
    try:
        b.log_tail = await _capture_log_tail(b)
    except Exception:
        BACKEND_ERRORS.labels(stage="log_capture_build").inc()
        logger.warning(
            "log capture failed for build %s — continuing without log_tail",
            b.id,
            exc_info=True,
        )
    b.finished_at = datetime.now(UTC)
    await session.commit()
    ctx = await _user_context(session, b.triggered_by_id)
    if ctx is not None:
        label = await _detector_label(session, b.detector_id)
        asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
            notify_build_failed(
                user_name=ctx.name,
                user_discord_id=ctx.discord_id,
                detector_label=label,
                git_tag=b.git_tag,
                failure_reason=reason,
                build_url=_ui_url(f"/detectors/{b.detector_id}"),
            )
        )
    await _cleanup_build_secret(b.id)


async def _handle_timeout(session: AsyncSession, b: DetectorBuild) -> None:
    try:
        batch_v1().delete_namespaced_job(
            name=b.k8s_job_name,
            namespace=settings.BUILD_NAMESPACE,
            propagation_policy="Background",
        )
    except ApiException as exc:
        # 404 is expected (the Job already disappeared). Anything else is a
        # real cluster error we want to see in metrics + logs rather than
        # silently drop on the floor.
        if exc.status != 404:
            BACKEND_ERRORS.labels(stage="k8s_cleanup").inc()
            logger.warning(
                "k8s build-job cleanup returned %s for build %s",
                exc.status,
                b.id,
                exc_info=True,
            )
    b.status = DetectorBuildStatus.TIMEOUT
    b.failure_reason = "build exceeded timeout"
    b.finished_at = datetime.now(UTC)
    await session.commit()
    ctx = await _user_context(session, b.triggered_by_id)
    if ctx is not None:
        label = await _detector_label(session, b.detector_id)
        asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
            notify_build_failed(
                user_name=ctx.name,
                user_discord_id=ctx.discord_id,
                detector_label=label,
                git_tag=b.git_tag,
                failure_reason="build exceeded timeout",
                build_url=_ui_url(f"/detectors/{b.detector_id}"),
            )
        )
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
        for ic in pod.status.init_container_statuses or []:
            if ic.state.terminated and ic.state.terminated.exit_code != 0:
                return f"{ic.name}_failed: exit={ic.state.terminated.exit_code}"
        for cs in pod.status.container_statuses or []:
            if cs.state.terminated and cs.state.terminated.exit_code != 0:
                return f"{cs.name}_failed: exit={cs.state.terminated.exit_code}"
        return "unknown_failure"
    except ApiException:
        return "k8s_api_error"


async def _cleanup_build_secret(build_id) -> None:
    try:
        core_v1().delete_namespaced_secret(
            name=build_secret_name(build_id),
            namespace=settings.BUILD_NAMESPACE,
        )
    except ApiException as exc:
        if exc.status != 404:
            BACKEND_ERRORS.labels(stage="k8s_cleanup").inc()
            logger.warning(
                "build secret cleanup returned %s for build %s",
                exc.status,
                build_id,
                exc_info=True,
            )
