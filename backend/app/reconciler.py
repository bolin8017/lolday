import asyncio
import contextlib
import csv
import io
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanStatus
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    batch_v1,
    core_v1,
    volcano_v1alpha1,
)
from app.services.manifest_store import ManifestDecodeError, decode_manifest_label
from app.services.notify import (
    notify_build_completed,
    notify_build_failed,
    notify_job_completed,
    notify_job_failed,
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


# ---- notify helpers ----------------------------------------------------------


@dataclass(frozen=True)
class NotifyContext:
    """Discord-embed identity for a single notification.

    Returned from :func:`_user_context`; ``None`` from that helper means
    "skip notify" (the user is a CF Access service-token principal whose
    events would only dilute the user-event channel).
    """

    name: str
    discord_id: str | None


async def _user_context(session: AsyncSession, user_id) -> NotifyContext | None:
    """Resolve a notification identity, or ``None`` to signal "skip notify".

    ``name`` falls back through display_name → email local-part → literal
    "user" (the last case only triggers when the user row is missing
    entirely, since email is required on User).

    Service-token principals yield ``None`` so every notify_* callsite
    can early-return. Service-token activity is automated and not
    actionable by humans — its events would only dilute the user-event
    Discord channel.
    """
    from app.models import User

    user = await session.get(User, user_id)
    if user is None:
        return NotifyContext(name="unknown", discord_id=None)
    if user.is_service_token:
        return None
    name = user.display_name or (user.email.split("@")[0] if user.email else "user")
    return NotifyContext(name=name, discord_id=user.discord_user_id)


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
        if isinstance(val, int | float):
            return (key, float(val))
    return None


async def _fire_job_failed_notify(
    session: AsyncSession,
    j,
    reason: str,
) -> None:
    """Schedule a job-failed Discord notify without blocking the reconciler.

    Shared helper for the 3 terminal-failure paths: Volcano Failed/Aborted
    phase, wall-clock TIMEOUT, and k8s_job_missing (404 on GET).
    """
    from app.models import DatasetConfig, DetectorVersion

    ctx = await _user_context(session, j.owner_id)
    if ctx is None:
        return
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det_label = await _detector_label(session, dv.detector_id) if dv else "unknown"
    detector_label = f"{det_label} {dv.git_tag}" if dv else det_label
    dataset_name = None
    ds_id = j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id
    if ds_id:
        ds = await session.get(DatasetConfig, ds_id)
        dataset_name = ds.name if ds else None
    asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
        notify_job_failed(
            user_name=ctx.name,
            user_discord_id=ctx.discord_id,
            job_type=j.type.value,
            detector_label=detector_label,
            dataset_name=dataset_name,
            failure_reason=reason,
            job_url=_ui_url(f"/jobs/{j.id}"),
        )
    )


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


def _container_from_failure_reason(failure_reason: str | None) -> str | None:
    """Extract container name from a failure_reason string like 'clone_failed: exit=1'."""
    if not failure_reason:
        return None
    head = failure_reason.split(":", 1)[0].strip()
    if head.endswith("_failed"):
        return head.removesuffix("_failed")
    return None


async def _capture_pod_logs(
    *,
    namespace: str,
    label_selector: str,
    main_container: str,
    init_containers: tuple[str, ...],
    failure_reason: str | None,
    tail_bytes: int,
    tail_lines: int = 200,
) -> str:
    """Capture log tail from the failing or main container of a labeled pod.

    Phase 13a A2: previous implementations hard-coded the container name
    (kaniko vs buildkit; detector only) and could not surface init-container
    output when the build/job failed before main started. This generic
    helper:
      1. Tries the container hinted by failure_reason first (e.g.
         'validate_failed' → 'validate').
      2. Falls back to main_container.
      3. Falls back to each init_container in order.
      4. Concatenates whatever logs were retrievable, prefixed with a
         '[<container>]' header line so the reader can tell what's what.
      5. Returns "" if no logs are retrievable from any container.

    The result is truncated to `tail_bytes` from the end so the persisted
    log_tail column doesn't blow up.
    """
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
    except ApiException:
        return ""
    if not pods.items:
        return ""
    pod = pods.items[0]

    # Build the container query order
    hinted = _container_from_failure_reason(failure_reason)
    order: list[str] = []
    if hinted and (hinted == main_container or hinted in init_containers):
        order.append(hinted)
    if main_container not in order:
        order.append(main_container)
    for ic in init_containers:
        if ic not in order:
            order.append(ic)

    # Try each container in order; collect what we can.
    chunks: list[str] = []
    for container in order:
        try:
            log = core_v1().read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
        except ApiException:
            continue
        if log:
            chunks.append(f"[{container}]\n{log}")

    if not chunks:
        return ""
    combined = "\n\n".join(chunks)
    return combined[-tail_bytes:]


async def _capture_log_tail(b: DetectorBuild) -> str:
    """Capture build pod's log tail.

    Phase 13a A2: was hard-coded to container='kaniko' (wrong — actual
    name is 'buildkit'). Now uses the generic helper with init-container
    fallback for when builds fail in clone/validate.
    """
    return await _capture_pod_logs(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
        main_container="buildkit",
        init_containers=("clone", "validate"),
        failure_reason=b.failure_reason,
        tail_bytes=settings.BUILD_LOG_TAIL_BYTES,
    )


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


# =============================================================================
# Phase 4: Job + Model Registry reconciliation
# =============================================================================

from app.models.job import NON_TERMINAL_STATUSES, Job, JobStatus, JobType  # noqa: E402
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
            j.finished_at = datetime.now(UTC)
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
        await session.commit()
        await _fire_job_failed_notify(session, j, "detector_timeout")
        await _cleanup_job_secret(j)
        return

    # Phase 11b: trust stage_end event before consulting Volcano phase.
    event_status = await _check_event_terminal(session, j.id)
    if event_status == "success":
        await _handle_job_succeeded(session, j)
        return
    if event_status == "failure":
        await _handle_job_failed(session, j)
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


async def _project_summary_metrics(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Aggregate last-per-name metric events + latest confusion_matrix event for
    ``job_id`` into ``Job.summary_metrics``. Idempotent — running twice produces
    the same result.

    Phase 11e: ``job_events`` is the canonical source of truth for run-time
    metrics; ``Job.summary_metrics`` is a single-writer materialized read model
    populated here on stage_end. MLflow remains the long-term store but is no
    longer the authoritative source for the lolday UI summary card.
    Phase 13b: adds per_class (from BinaryClassification.evaluate emit).
    """
    from app.models import JobEvent

    rows = (
        await session.execute(
            select(JobEvent.kind, JobEvent.payload, JobEvent.ts)
            .where(JobEvent.job_id == job_id)
            .where(JobEvent.kind.in_(["metric", "confusion_matrix", "per_class"]))
            .order_by(JobEvent.ts.asc())
        )
    ).all()

    metrics: dict[str, float] = {}
    confusion_matrix: dict[str, Any] | None = None
    per_class: dict[str, Any] | None = None
    for kind, payload, _ts in rows:
        if kind == "metric":
            try:
                metrics[payload["name"]] = float(payload["value"])
            except (KeyError, TypeError, ValueError):
                continue
        elif kind == "confusion_matrix":
            try:
                confusion_matrix = {
                    "labels": payload["labels"],
                    "matrix": payload["matrix"],
                }
            except KeyError:
                continue
        elif kind == "per_class":
            payload_per_class = payload.get("per_class")
            if isinstance(payload_per_class, dict):
                per_class = payload_per_class

    job = await session.get(Job, job_id)
    job.summary_metrics = {
        "metrics": metrics,
        "confusion_matrix": confusion_matrix,
        "per_class": per_class,
    }
    await session.commit()


async def _read_mlflow_artifact(run_id: str, path: str) -> str:
    """Fetch an MLflow artifact text body via the tracking server proxy.

    Returns the raw text content. Raises ``FileNotFoundError`` on 404 so
    the caller can decide whether to skip silently (predict jobs that
    legitimately lack a ``predictions.csv`` should not surface as an
    error).
    """
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/runs/get?run_id={run_id}"
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        run_resp = await c.get(url)
        run_resp.raise_for_status()
        artifact_uri: str = run_resp.json()["run"]["info"]["artifact_uri"]

    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise RuntimeError(f"unexpected artifact_uri scheme: {artifact_uri!r}")
    relative = artifact_uri[len(prefix) :].rstrip("/")
    download_url = (
        f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/"
        f"{relative}/{path}"
    )
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(download_url)
    if r.status_code == 404:
        raise FileNotFoundError(path)
    r.raise_for_status()
    return r.text


async def _project_prediction_summary(session: AsyncSession, j: Job) -> None:
    """Read predictions.csv via MLflow artifacts on a succeeded predict job,
    compute total + class-distribution + duration, cache into
    ``Job.summary_metrics["prediction_summary"]``.

    Errors are logged + counted via ``BACKEND_ERRORS`` and never raised —
    projection failure is observability tech debt, not a state-machine
    issue. Job remains SUCCEEDED; the frontend falls back to a recompute
    endpoint (Task 1.3) when the cache is absent.
    """
    if not j.mlflow_run_id:
        return
    try:
        csv_text = await _read_mlflow_artifact(j.mlflow_run_id, "predictions.csv")
    except FileNotFoundError:
        return
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_artifact_read").inc()
        logger.exception(
            "prediction_summary artifact read failed",
            extra={"job_id": str(j.id)},
        )
        return

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
    except csv.Error:
        BACKEND_ERRORS.labels(stage="prediction_summary_csv_parse").inc()
        logger.exception(
            "prediction_summary csv parse failed",
            extra={"job_id": str(j.id)},
        )
        return

    # maldet binary-classification evaluator writes `pred_label` per the
    # framework's prediction-CSV contract (alongside pred_score and per-class
    # probabilities). Detectors that emit a non-standard CSV are silently
    # skipped — better to render no card than wrong counts.
    if not reader.fieldnames or "pred_label" not in reader.fieldnames:
        return
    distribution = Counter(row["pred_label"] for row in rows)
    total = len(rows)
    duration_seconds = (
        (j.finished_at - j.started_at).total_seconds()
        if (j.started_at and j.finished_at)
        else None
    )

    sm = dict(j.summary_metrics or {})
    sm["prediction_summary"] = {
        "total": total,
        "distribution": {str(k): int(v) for k, v in distribution.items()},
        "duration_seconds": duration_seconds,
    }
    j.summary_metrics = sm
    await session.commit()


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
            j.started_at = datetime.now(UTC)
        await session.commit()


async def _handle_job_succeeded(session: AsyncSession, j: Job) -> None:
    # Phase 11e: summary_metrics is no longer sourced from MLflow — the
    # `_project_summary_metrics` projection below reads from the canonical
    # job_events stream. We still need an MlflowClient for the downstream
    # model-registration call.
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)

    log_tail = await _capture_job_log_tail(j)

    j.log_tail = log_tail
    j.status = JobStatus.SUCCEEDED
    j.finished_at = datetime.now(UTC)

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
    j.finished_at = datetime.now(UTC)
    await session.commit()

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
        pods = core_v1().list_namespaced_pod(
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


async def _capture_job_log_tail(j: Job) -> str:
    """Capture job pod's log tail.

    Phase 13a A2: previously read main 'detector' container only. Now
    also captures init-container logs (config-writer, model-fetcher) when
    the job fails before main starts.
    """
    return await _capture_pod_logs(
        namespace=settings.JOB_NAMESPACE,
        label_selector=f"lolday.job-id={j.id}",
        main_container="detector",
        init_containers=("config-writer", "model-fetcher"),
        failure_reason=j.failure_reason,
        tail_bytes=8192,
    )


async def _cleanup_job_secret(j: Job) -> None:
    try:
        from app.services.job_spec import _job_token_secret_name

        core_v1().delete_namespaced_secret(
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


ORPHAN_GRACE_SECONDS = 300  # don't touch a vcjob younger than this — see below.


async def reconcile_orphan_vcjobs(session: AsyncSession) -> int:
    """Delete Volcano Jobs whose ``lolday.job-id`` label has no matching DB row.

    A schema migration / DB rebuild can leave Volcano Jobs in K8s that the
    backend no longer knows about. Their init container then dies on every
    pod with "job not found", the pod stays Init:Error indefinitely, and
    KubeContainerWaiting fires forever. This pass closes that loop.

    Race-window guard: ``app.routers.jobs`` flushes the Job DB row, calls
    ``volcano_v1alpha1().create_namespaced_custom_object()``, then commits.
    A reconciler running with an independent session at PostgreSQL
    READ COMMITTED would not see the uncommitted row and could delete
    the freshly-created vcjob. Skipping vcjobs younger than
    ``ORPHAN_GRACE_SECONDS`` is enough headroom for the API request to
    finish committing, and the next pass picks up genuinely-orphaned ones.

    Listing failures bubble up — the surrounding ``reconciler_loop`` already
    logs + counts iteration failures consistently with reconcile_build /
    reconcile_job / sync_model_versions.

    Returns the number of orphans deleted, for metrics.
    """
    from app.services.job_spec import _job_token_secret_name

    listing = volcano_v1alpha1().list_namespaced_custom_object(
        group=VOLCANO_BATCH_GROUP,
        version=VOLCANO_BATCH_VERSION,
        namespace=settings.JOB_NAMESPACE,
        plural=VOLCANO_JOB_PLURAL,
    )

    now = datetime.now(UTC)
    deleted = 0
    for vjob in listing.get("items", []):
        meta = vjob.get("metadata", {}) or {}
        name = meta.get("name", "")
        # Volcano stamps the same labels both at the job level and on the
        # task pod template — read the top-level copy first (survives task
        # restructuring), with the deeper path as a fallback for older
        # vcjobs / chart variants that only set it on the pod template.
        label = (meta.get("labels") or {}).get("lolday.job-id")
        if not label:
            tasks = vjob.get("spec", {}).get("tasks") or []
            if tasks:
                label = (
                    (tasks[0].get("template") or {})
                    .get("metadata", {})
                    .get("labels", {})
                    .get("lolday.job-id")
                )
        if not label:
            continue
        try:
            job_uuid = uuid.UUID(label)
        except ValueError:
            BACKEND_ERRORS.labels(stage="orphan_vcjob_malformed_label").inc()
            logger.warning("vcjob %s has malformed lolday.job-id %r", name, label)
            continue

        created_at_raw = meta.get("creationTimestamp")
        if created_at_raw:
            try:
                created_at = datetime.fromisoformat(
                    created_at_raw.replace("Z", "+00:00")
                )
            except ValueError:
                created_at = None
            if created_at and (now - created_at).total_seconds() < ORPHAN_GRACE_SECONDS:
                continue

        from app.models.job import Job  # avoid circular import at module load

        exists = await session.scalar(select(Job.id).where(Job.id == job_uuid))
        if exists is not None:
            continue

        vcjob_gone = False
        try:
            volcano_v1alpha1().delete_namespaced_custom_object(
                group=VOLCANO_BATCH_GROUP,
                version=VOLCANO_BATCH_VERSION,
                namespace=settings.JOB_NAMESPACE,
                plural=VOLCANO_JOB_PLURAL,
                name=name,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status == 404:
                vcjob_gone = True
            else:
                BACKEND_ERRORS.labels(stage="orphan_vcjob_delete").inc()
                logger.warning(
                    "orphan vcjob %s delete returned %s",
                    name,
                    exc.status,
                    exc_info=True,
                )
                continue

        # Reach the secret cleanup whether vcjob deleted just now or was
        # already gone — the orphan secret outlives a partial delete.
        try:
            core_v1().delete_namespaced_secret(
                name=_job_token_secret_name(job_uuid),
                namespace=settings.JOB_NAMESPACE,
            )
        except ApiException as exc:
            if exc.status != 404:
                BACKEND_ERRORS.labels(stage="orphan_secret_delete").inc()
                logger.warning(
                    "orphan secret for vcjob %s delete returned %s",
                    name,
                    exc.status,
                    exc_info=True,
                )

        if not vcjob_gone:
            deleted += 1
        logger.info("deleted orphan vcjob %s (job-id %s)", name, job_uuid)

    return deleted


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
            mv.last_transitioned_at = datetime.now(UTC)
    await session.commit()
