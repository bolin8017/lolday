import asyncio
import contextlib
import logging
from datetime import UTC, datetime

import httpx
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
from app.models.job import NON_TERMINAL_STATUSES, Job
from app.reconciler.jobs import (
    _handle_job_failed as _handle_job_failed,
)
from app.reconciler.jobs import (
    _handle_job_succeeded as _handle_job_succeeded,
)
from app.reconciler.jobs import (
    reconcile_job,
)
from app.reconciler.log_capture import (
    _capture_log_tail,
)
from app.reconciler.log_capture import (
    _capture_pod_logs as _capture_pod_logs,
)
from app.reconciler.log_capture import (
    _container_from_failure_reason as _container_from_failure_reason,
)
from app.reconciler.model_sync import sync_model_versions
from app.reconciler.notify import (
    NotifyContext as NotifyContext,
)
from app.reconciler.notify import (
    _detector_label,
    _ui_url,
    _user_context,
)
from app.reconciler.notify import (
    _fire_job_failed_notify as _fire_job_failed_notify,
)
from app.reconciler.orphans import ORPHAN_GRACE_SECONDS as ORPHAN_GRACE_SECONDS
from app.reconciler.orphans import reconcile_orphan_vcjobs
from app.reconciler.projections import (
    _project_prediction_summary as _project_prediction_summary,
)
from app.reconciler.projections import (
    _project_summary_metrics as _project_summary_metrics,
)
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanStatus
from app.services.k8s import (
    batch_v1,
    core_v1,
)
from app.services.manifest_store import ManifestDecodeError, decode_manifest_label
from app.services.notify import (
    notify_build_completed,
    notify_build_failed,
    notify_trivy_blocked,
)

logger = logging.getLogger(__name__)

IN_FLIGHT = {
    DetectorBuildStatus.PENDING,
    DetectorBuildStatus.CLONING,
    DetectorBuildStatus.VALIDATING,
    DetectorBuildStatus.BUILDING,
    DetectorBuildStatus.SCANNING,
}


# Loop tuning. Module-level so tests can monkeypatch to collapse iteration time.
SYNC_EVERY_N_ITERATIONS = 6
ORPHAN_SCAN_EVERY_N_ITERATIONS = 30  # ~5 min at the default 10s wait
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

    if scan.critical > 0:
        await harbor.delete_artifact("detectors", detector.name, digest)
        b.status = DetectorBuildStatus.CVE_BLOCKED
        b.failure_reason = f"cve_blocked: critical={scan.critical} high={scan.high}"
        b.trivy_critical = scan.critical
        b.trivy_high = scan.high
        b.finished_at = datetime.now(UTC)
        await session.commit()
        ctx = await _user_context(session, b.triggered_by_id)
        if ctx is not None:
            label = await _detector_label(session, b.detector_id)
            asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
                notify_trivy_blocked(
                    user_name=ctx.name,
                    user_discord_id=ctx.discord_id,
                    detector_label=label,
                    git_tag=b.git_tag,
                    cve_summary=f"{scan.critical} critical, {scan.high} high",
                    build_url=_ui_url(f"/detectors/{b.detector_id}"),
                )
            )
    else:
        # A DetectorVersion may already exist for this (detector_id, git_tag)
        # from a prior build of the same tag. Two legitimate replay paths:
        #
        #   A. Long-stuck `scanning` build finishes after a newer build has
        #      already produced the version row → same image, same digest.
        #      Drop-in no-op; preserve existing version.
        #   B. Git tag was force-pushed and a second build legitimately
        #      produces a different artifact. We refuse to rebind the tag
        #      silently — the build is marked FAILED with a clear reason
        #      and the operator must bump the tag or delete the existing
        #      version first. This surfaces the anomaly instead of handing
        #      the user stale inference results.
        existing_version = (
            await session.execute(
                select(DetectorVersion).where(
                    DetectorVersion.detector_id == b.detector_id,
                    DetectorVersion.git_tag == b.git_tag,
                )
            )
        ).scalar_one_or_none()
        if existing_version is None:
            # Fetch OCI image labels for the just-scanned artifact and extract
            # the maldet manifest. A detector without `io.maldet.manifest`
            # cannot be driven by Phase 11b's stage pipeline, so we fail the
            # build closed rather than creating a DetectorVersion with
            # manifest=NULL that would quietly explode downstream.
            try:
                labels = await harbor.get_image_labels(
                    project="detectors",
                    repository=detector.name,
                    digest=digest,
                )
            except Exception:
                BACKEND_ERRORS.labels(stage="harbor_labels_fetch").inc()
                logger.exception(
                    "failed to fetch image labels", extra={"build_id": str(b.id)}
                )
                b.status = DetectorBuildStatus.FAILED
                b.failure_reason = "harbor_labels_fetch_failed"
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
                            failure_reason="harbor_labels_fetch_failed",
                            build_url=_ui_url(f"/detectors/{b.detector_id}"),
                        )
                    )
                await _cleanup_build_secret(b.id)
                return

            manifest_label = labels.get("io.maldet.manifest")
            if not manifest_label:
                BACKEND_ERRORS.labels(stage="manifest_missing").inc()
                logger.error(
                    "build image has no io.maldet.manifest label",
                    extra={"build_id": str(b.id)},
                )
                b.status = DetectorBuildStatus.FAILED
                b.failure_reason = "manifest_label_missing"
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
                            failure_reason="manifest_label_missing",
                            build_url=_ui_url(f"/detectors/{b.detector_id}"),
                        )
                    )
                await _cleanup_build_secret(b.id)
                return

            try:
                manifest_model = decode_manifest_label(manifest_label)
            except ManifestDecodeError as exc:
                BACKEND_ERRORS.labels(stage="manifest_invalid").inc()
                logger.error(
                    "manifest decode failed",
                    extra={"build_id": str(b.id), "err": str(exc)},
                )
                b.status = DetectorBuildStatus.FAILED
                b.failure_reason = "manifest_invalid"
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
                            failure_reason="manifest_invalid",
                            build_url=_ui_url(f"/detectors/{b.detector_id}"),
                        )
                    )
                await _cleanup_build_secret(b.id)
                return

            manifest_dict = manifest_model.model_dump(mode="json")

            # The buildkit container stamps the commit SHA into the standard
            # OCI label ``org.opencontainers.image.revision`` via a
            # platform-emitted ``--label`` flag (services/build.py — the sh
            # wrapper around buildctl-daemonless.sh). This is the canonical
            # post-build source of truth — no callback writes ``b.git_sha``
            # after Phase 11c removed the v0 schema-POST route. Fail closed
            # if the label is missing so we never persist a DetectorVersion
            # with empty git_sha.
            commit_sha = labels.get("org.opencontainers.image.revision", "")
            if not commit_sha:
                BACKEND_ERRORS.labels(stage="git_sha_label_missing").inc()
                logger.error(
                    "build image has no org.opencontainers.image.revision label",
                    extra={"build_id": str(b.id)},
                )
                b.status = DetectorBuildStatus.FAILED
                b.failure_reason = "git_sha_label_missing"
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
                            failure_reason="git_sha_label_missing",
                            build_url=_ui_url(f"/detectors/{b.detector_id}"),
                        )
                    )
                await _cleanup_build_secret(b.id)
                return

            version = DetectorVersion(
                detector_id=b.detector_id,
                git_tag=b.git_tag,
                git_sha=commit_sha,
                harbor_image=f"{settings.HARBOR_IMAGE_PREFIX}/detectors/{detector.name}:{b.git_tag}",
                image_digest=digest,
                manifest=manifest_dict,
                status=DetectorVersionStatus.ACTIVE,
            )
            session.add(version)
            b.git_sha = commit_sha
        else:
            if existing_version.image_digest != digest:
                BACKEND_ERRORS.labels(stage="detector_version_digest_mismatch").inc()
                logger.warning(
                    "digest divergence for (detector_id=%s, tag=%s): "
                    "existing=%s new=%s — refusing to rebind",
                    b.detector_id,
                    b.git_tag,
                    existing_version.image_digest,
                    digest,
                )
                b.status = DetectorBuildStatus.FAILED
                b.failure_reason = (
                    f"tag {b.git_tag!r} already bound to digest "
                    f"{existing_version.image_digest[:19]}…; refusing to rebind to "
                    f"{digest[:19]}… — bump tag or delete existing version first"
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
                            failure_reason=b.failure_reason,
                            build_url=_ui_url(f"/detectors/{b.detector_id}"),
                        )
                    )
                await _cleanup_build_secret(b.id)
                return
            commit_sha = existing_version.git_sha or ""
            b.git_sha = commit_sha
            logger.warning(
                "detector_version replay for (%s, %s) digest=%s — "
                "idempotent no-op on the existing row",
                b.detector_id,
                b.git_tag,
                digest,
            )
        b.status = DetectorBuildStatus.SUCCEEDED
        b.trivy_critical = scan.critical
        b.trivy_high = scan.high
        # Phase 13a A2 follow-up: succeeded builds were never capturing
        # log_tail (only the failure path did). Symmetric with how
        # _handle_job_succeeded / _handle_job_failed both capture; users
        # legitimately want to see buildkit progress for green builds too.
        # Wrapped in try/except: if K8s API is misbehaving (non-ApiException
        # exception, e.g. unexpected None pod metadata) we must not block
        # the build's terminal-state commit, otherwise the build would
        # spin in SCANNING until BUILD_TIMEOUT_SECONDS marks it TIMEOUT.
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
                notify_build_completed(
                    user_name=ctx.name,
                    user_discord_id=ctx.discord_id,
                    detector_label=label,
                    git_tag=b.git_tag,
                    commit_sha=commit_sha,
                    build_url=_ui_url(f"/detectors/{b.detector_id}"),
                )
            )
    await _cleanup_build_secret(b.id)


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
                        logger.exception(
                            "reconcile_build failed", extra={"build_id": str(b.id)}
                        )

                # Job reconcile pass (Phase 4)
                res_jobs = await session.execute(
                    select(Job).where(Job.status.in_(NON_TERMINAL_STATUSES))
                )
                for j in res_jobs.scalars().all():
                    try:
                        await reconcile_job(session, j)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_job").inc()
                        logger.exception(
                            "reconcile_job failed", extra={"job_id": str(j.id)}
                        )

                # Model version sync every N iterations (~60s at default N=6)
                if iteration % SYNC_EVERY_N_ITERATIONS == 0:
                    try:
                        await sync_model_versions(session)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="sync_model_versions").inc()
                        logger.exception("sync_model_versions failed")

                # Orphan vcjob scan (~5 min at default N=30)
                if iteration % ORPHAN_SCAN_EVERY_N_ITERATIONS == 0:
                    try:
                        await reconcile_orphan_vcjobs(session)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_orphan_vcjobs").inc()
                        logger.exception("reconcile_orphan_vcjobs failed")
        except Exception:
            BACKEND_ERRORS.labels(stage="reconciler_iteration").inc()
            logger.exception("reconciler iteration failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=RECONCILER_WAIT_SECONDS)
    logger.info("reconciler stopped")
