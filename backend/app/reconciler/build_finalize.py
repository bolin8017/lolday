"""Post-scan-SUCCESS finalization extracted from _handle_succeeded.

Hosts :func:`_finalize_clean_scan`. Called by ``builds._handle_succeeded``
when ``scan.status == SUCCESS``. Behavior preserved 1:1.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.detector import (
    Detector,
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.reconciler.log_capture import _capture_log_tail
from app.reconciler.notify import _detector_label, _ui_url, _user_context
from app.services.harbor import HarborClient
from app.services.manifest_store import ManifestDecodeError, decode_manifest_label
from app.services.notify import (
    notify_build_completed,
    notify_build_failed,
    notify_trivy_blocked,
)

logger = logging.getLogger(__name__)


async def _finalize_clean_scan(
    session: AsyncSession,
    b: DetectorBuild,
    harbor: HarborClient,
    detector: Detector,
    digest: str,
    scan,
) -> None:
    """CVE-block or promote a build whose Harbor scan returned SUCCESS.

    scan.critical > 0 → CVE_BLOCKED + artifact deletion.
    else → promotion path (idempotency check, manifest decode, version row).
    5 fail-closed branches: harbor_labels_fetch_failed, manifest_label_missing,
    manifest_invalid, git_sha_label_missing, digest mismatch.
    Always calls _cleanup_build_secret on exit (inline on early returns).
    """
    # Lazy import to avoid circular dep with builds.py (which imports _finalize_clean_scan
    # at module top from this file).
    from app.reconciler.builds import _cleanup_build_secret

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
