import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.models.detector import (
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanResult, ScanStatus
from app.services.k8s import batch_v1, core_v1

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
            b.finished_at = datetime.now(timezone.utc)
            await session.commit()
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
    await _cleanup_build_secret(b.id)


async def _handle_failed(session: AsyncSession, b: DetectorBuild, job) -> None:
    reason = await _extract_failure_reason(b)
    b.status = DetectorBuildStatus.FAILED
    b.failure_reason = reason
    b.log_tail = await _capture_log_tail(b)
    b.finished_at = datetime.now(timezone.utc)
    await session.commit()
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
    logger.info("build reconciler started")
    while not stop_event.is_set():
        try:
            async with async_session_maker() as session:
                res = await session.execute(
                    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
                )
                for b in res.scalars().all():
                    try:
                        await reconcile_build(session, b)
                    except Exception:
                        logger.exception("reconcile_build failed", extra={"build_id": str(b.id)})
        except Exception:
            logger.exception("reconciler iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
    logger.info("build reconciler stopped")
