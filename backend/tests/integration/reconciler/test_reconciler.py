from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.models.detector import DetectorBuild, DetectorBuildStatus


@pytest.mark.asyncio
async def test_reconcile_succeeded_job_moves_to_scanning(db_session):
    from app.models.detector import Detector
    from app.reconciler import reconcile_build

    detector = Detector(
        name="testdet",
        display_name="Test",
        git_url="https://github.com/x/y.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-abc",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.builds.batch_v1") as bv:
        bv.return_value.read_namespaced_job.return_value = fake_job
        # harbor scan pending
        with patch("app.reconciler.builds.HarborClient") as hc:
            hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:abc")
            from app.services.harbor import ScanResult, ScanStatus

            hc.return_value.get_scan = AsyncMock(
                return_value=ScanResult(ScanStatus.PENDING, 0, 0, 0, 0)
            )
            await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.SCANNING


@pytest.mark.asyncio
async def test_reconcile_cve_blocked(db_session):
    from app.models.detector import Detector
    from app.reconciler import reconcile_build

    detector = Detector(
        name="testdet2",
        display_name="Test2",
        git_url="https://github.com/x/z.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-xyz",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1") as _cv,
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        from app.services.harbor import ScanResult, ScanStatus

        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:deadbeef")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(
                ScanStatus.SUCCESS, critical=1, high=0, medium=0, low=0
            )
        )
        hc.return_value.delete_tag_or_artifact = AsyncMock()
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.CVE_BLOCKED
    assert build.trivy_critical == 1
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_timeout(db_session):
    from datetime import datetime, timedelta

    from app.reconciler import reconcile_build

    build = DetectorBuild(
        detector_id=uuid4(),
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-timeout",
        status=DetectorBuildStatus.BUILDING,
    )
    # started_at far in the past
    build.started_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 0
    fake_job.status.failed = 0

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        bv.return_value.delete_namespaced_job.return_value = None
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.TIMEOUT
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_not_scanned_triggers_trivy_scan(db_session):
    """Phase 8.1: Harbor does not auto-scan on push. Reconciler must call
    trigger_scan() when status=NotScanned and flip build to SCANNING.
    A regression to the old `{PENDING, RUNNING, NOT_SCANNED}` branch
    would pass status-check tests but skip the actual scan kick-off —
    explicit trigger_scan call-count assertion here catches that.
    """
    from app.models.detector import Detector
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="tds1",
        display_name="tds1",
        git_url="https://github.com/x/s1.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds1",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    trigger_calls = []

    async def _capture_trigger(project, repo, digest):
        trigger_calls.append((project, repo, digest))

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:new")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.NOT_SCANNED, 0, 0, 0, 0)
        )
        hc.return_value.trigger_scan = AsyncMock(side_effect=_capture_trigger)
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.SCANNING
    assert build.finished_at is None  # critical: still in-flight
    assert trigger_calls == [("detectors", "tds1", "sha256:new")]


@pytest.mark.asyncio
async def test_reconcile_trigger_scan_failure_keeps_build_status_and_counts_metric(
    db_session,
):
    """If Harbor is unreachable or 500s, trigger_scan raises httpx.HTTPError.
    Reconciler must log + increment metric + RETURN WITHOUT flipping
    status to SCANNING, so the next reconcile tick retries cleanly
    (the old bool-returning API silently flipped status regardless).
    """
    import httpx
    from app.metrics import BACKEND_ERRORS
    from app.models.detector import Detector
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="tds2",
        display_name="tds2",
        git_url="https://github.com/x/s2.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds2",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()
    original_status = build.status

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(stage="harbor_trigger_scan")._value.get()

    async def _raise(*a, **kw):
        raise httpx.HTTPStatusError(
            "500",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:x")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.NOT_SCANNED, 0, 0, 0, 0)
        )
        hc.return_value.trigger_scan = AsyncMock(side_effect=_raise)
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == original_status  # did NOT flip to SCANNING
    assert build.finished_at is None
    after = BACKEND_ERRORS.labels(stage="harbor_trigger_scan")._value.get()
    assert after == before + 1  # metric incremented


@pytest.mark.asyncio
async def test_reconcile_build_scan_error_retriggers_and_does_not_promote(db_session):
    """Regression: scan_status=Error must retrigger (not promote).

    Prior code treated Error as Success-with-0-CVEs — a false-negative caused
    by transient Trivy DB cache-lock timeouts (observed in production
    2026-04-22 01:37:48). The paired test
    `test_reconcile_persistent_scan_error_eventually_times_out` locks the
    wall-clock bound on retries.
    """
    from app.metrics import BACKEND_ERRORS
    from app.models.detector import Detector, DetectorVersion
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus
    from sqlalchemy import select

    detector = Detector(
        name="tds-err",
        display_name="tds-err",
        git_url="https://github.com/x/err.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds-err",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    trigger_calls = []

    async def _capture_trigger(project, repo, digest):
        trigger_calls.append((project, repo, digest))

    before_metric = BACKEND_ERRORS.labels(stage="harbor_scan_error_retry")._value.get()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:errdigest")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.ERROR, 0, 0, 0, 0)
        )
        hc.return_value.trigger_scan = AsyncMock(side_effect=_capture_trigger)
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    # 1. Build stays non-terminal (SCANNING for retry, never SUCCEEDED)
    assert build.status == DetectorBuildStatus.SCANNING
    assert build.finished_at is None
    # 2. No DetectorVersion was created — image was NOT promoted
    rows = (
        (
            await db_session.execute(
                select(DetectorVersion).where(
                    DetectorVersion.detector_id == detector.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []
    # 3. trigger_scan called with the right digest — retry path invoked
    assert trigger_calls == [("detectors", "tds-err", "sha256:errdigest")]
    # 4. Error-retry metric incremented so operators can correlate with Trivy logs
    after_metric = BACKEND_ERRORS.labels(stage="harbor_scan_error_retry")._value.get()
    assert after_metric == before_metric + 1


@pytest.mark.asyncio
async def test_reconcile_persistent_scan_error_eventually_times_out(db_session):
    """Bound on the Phase 9.5 Error-retry loop.

    reconcile_build dispatches on `job.status.succeeded` FIRST, so a build
    whose k8s Job long-since succeeded but whose Harbor scan persistently
    returns Error would otherwise loop through _handle_succeeded forever —
    the elif at the original BUILD_TIMEOUT check was unreachable. This
    test pins the fix: wall-clock > BUILD_TIMEOUT_SECONDS + 60 must route
    to _handle_timeout regardless of job phase.
    """
    from datetime import datetime, timedelta

    from app.config import settings
    from app.models.detector import Detector, DetectorVersion
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus
    from sqlalchemy import select

    detector = Detector(
        name="tds-err-to",
        display_name="tds-err-to",
        git_url="https://github.com/x/errto.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds-err-to",
        status=DetectorBuildStatus.SCANNING,
    )
    # Stuck in scan retry past the wall-clock ceiling
    build.started_at = datetime.now(UTC) - timedelta(
        seconds=settings.BUILD_TIMEOUT_SECONDS + 120
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1  # Build finished; only scan is stuck
    fake_job.status.failed = 0

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        bv.return_value.delete_namespaced_job.return_value = None
        # Harbor persistently reports Error — would cause infinite retry pre-fix
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:stuck")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.ERROR, 0, 0, 0, 0)
        )
        hc.return_value.trigger_scan = AsyncMock()
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.TIMEOUT
    assert build.finished_at is not None
    # No DetectorVersion slipped through promotion
    rows = (
        (
            await db_session.execute(
                select(DetectorVersion).where(
                    DetectorVersion.detector_id == detector.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_reconcile_dedup_on_existing_version_no_unbound_local(db_session):
    """Replay: a stale stuck-scanning build finishes after a newer build
    already produced the DetectorVersion row. Must mark SUCCEEDED
    without UnboundLocalError, preserve the existing row, and fire the
    build-completed notify with the existing commit SHA.
    """
    from app.models.detector import Detector, DetectorVersion, DetectorVersionStatus
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus
    from sqlalchemy import select

    detector = Detector(
        name="tds3",
        display_name="tds3",
        git_url="https://github.com/x/s3.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    existing = DetectorVersion(
        detector_id=detector.id,
        git_tag="v0.1.0",
        git_sha="deadbeef" * 5,
        harbor_image="harbor/tds3:v0.1.0",
        image_digest="sha256:winner",
        status=DetectorVersionStatus.ACTIVE,
    )
    db_session.add(existing)
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds3",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        # Same digest as existing — idempotent replay, not divergence
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:winner")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        # Must not raise UnboundLocalError
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.SUCCEEDED
    assert build.git_sha == existing.git_sha  # copied from existing
    # Still only one DetectorVersion for (det, v0.1.0)
    rows = (
        (
            await db_session.execute(
                select(DetectorVersion).where(
                    DetectorVersion.detector_id == detector.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].image_digest == "sha256:winner"


@pytest.mark.asyncio
async def test_reconcile_dedup_rejects_digest_divergence(db_session):
    """Force-pushed tag: existing row's digest differs from new build's.
    Must FAIL the new build with a clear reason rather than silently
    accept the stale existing image as authoritative.
    """
    from app.metrics import BACKEND_ERRORS
    from app.models.detector import Detector, DetectorVersion, DetectorVersionStatus
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="tds4",
        display_name="tds4",
        git_url="https://github.com/x/s4.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    existing = DetectorVersion(
        detector_id=detector.id,
        git_tag="v0.1.0",
        git_sha="oldsha" * 7,
        harbor_image="harbor/tds4:v0.1.0",
        image_digest="sha256:existing-version",
        status=DetectorVersionStatus.ACTIVE,
    )
    db_session.add(existing)
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-tds4",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(
        stage="detector_version_digest_mismatch"
    )._value.get()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(
            return_value="sha256:new-digest"
        )
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert "already bound to digest" in (build.failure_reason or "")
    after = BACKEND_ERRORS.labels(stage="detector_version_digest_mismatch")._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_reconcile_build_k8s_job_404_marks_failed_with_reason(
    db_session, seed_user
):
    """`read_namespaced_job` returning 404 means the K8s Job has been GC'd
    (or never created). Builds.py:55-74 marks the build FAILED with
    `k8s_job_missing` and (if owner is a real user) fires `notify_build_failed`.

    Pins the dedicated reason string for operator triage so a regression
    that silently keeps the build in BUILDING (the original bug pattern
    before the 404 branch existed) flips this test red.
    """
    from app.models.detector import Detector
    from app.reconciler import reconcile_build
    from kubernetes.client import ApiException

    detector = Detector(
        name="tds-404",
        display_name="tds-404",
        git_url="https://github.com/x/s404.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-404",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.side_effect = ApiException(
            status=404, reason="not found"
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "k8s_job_missing"
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_succeeded_with_missing_digest_marks_failed(
    db_session, seed_user
):
    """Build's K8s Job succeeded but Harbor reports no artifact at the
    expected (repo, tag) — `_handle_succeeded` (builds.py:112-117) fails the
    build with `artifact_missing_in_harbor` rather than looping forever
    asking for a scan of a non-existent digest.

    The scenario shows up when BuildKit's push raced Harbor's GC, or when
    the project / robot account changed mid-flight. Either way the reason
    string is the operator's signal to retrigger the build.
    """
    from app.models.detector import Detector
    from app.reconciler import reconcile_build

    detector = Detector(
        name="tds-no-digest",
        display_name="tds-no-digest",
        git_url="https://github.com/x/snod.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-no-digest",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value=None)
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "artifact_missing_in_harbor"
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_timeout_swallows_k8s_delete_500(db_session, seed_user):
    """`_handle_timeout` cleans up the build's K8s Job before flipping
    DetectorBuildStatus.TIMEOUT. The except block (builds.py:202-208)
    must bump `BACKEND_ERRORS{stage="k8s_cleanup"}` on a non-404 ApiException
    and continue — same diagnostic-error vs silent-swallow shape as
    `test_reconcile_job_timeout_swallows_volcano_delete_500` for vcjobs.
    """
    from datetime import datetime, timedelta

    from app.config import settings
    from app.metrics import BACKEND_ERRORS
    from app.models.detector import Detector
    from app.reconciler import reconcile_build
    from kubernetes.client import ApiException
    from prometheus_client import REGISTRY

    detector = Detector(
        name="tds-timeout-500",
        display_name="tds-timeout-500",
        git_url="https://github.com/x/sto500.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-timeout-500",
        status=DetectorBuildStatus.BUILDING,
    )
    build.started_at = datetime.now(UTC) - timedelta(
        seconds=settings.BUILD_TIMEOUT_SECONDS + 120
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 0
    fake_job.status.failed = 0

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "k8s_cleanup"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        bv.return_value.delete_namespaced_job.side_effect = ApiException(
            status=500, reason="server error"
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.TIMEOUT
    assert build.finished_at is not None
    # The 500 path bumps `k8s_cleanup` once (404 silent-swallows).
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="k8s_cleanup") is not None


# ---------------------------------------------------------------------------
# `_update_progress` happy paths — drives DetectorBuildStatus transitions
# based on which build-pod init container has terminated.
# ---------------------------------------------------------------------------


def _make_build_pod(finished_init_names: list[str]):
    """Build a minimal V1Pod-shaped stub for `_update_progress`.

    `finished_init_names` are the init-container names whose `.state.terminated`
    is non-None (treated as "finished" by builds.py:233).
    """

    class _Term:
        pass

    class _State:
        def __init__(self, finished: bool):
            self.terminated = _Term() if finished else None

    class _IC:
        def __init__(self, name: str, finished: bool):
            self.name = name
            self.state = _State(finished)

    class _Pod:
        class _Meta:
            name = "build-pod-xxx"

        metadata = _Meta()

        class _St:
            pass

        status = _St()

    p = _Pod()
    # Order matches the build pipeline: clone → validate → build.
    p.status.init_container_statuses = [
        _IC("clone", "clone" in finished_init_names),
        _IC("validate", "validate" in finished_init_names),
        _IC("build", "build" in finished_init_names),
    ]
    return p


def _patched_update_progress_pods(pods):
    """Patch `app.reconciler.builds.core_v1` to return the supplied pod list."""

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            class _R:
                items = pods  # stub class

            return _R()

    return patch("app.reconciler.builds.core_v1", return_value=_CoreStub())


@pytest.mark.asyncio
async def test_update_progress_no_pod_yet_returns_early(db_session, seed_user):
    """`_update_progress` is called once per reconcile tick while the build's
    K8s Job is in-flight. Before the pod is admitted, `list_namespaced_pod`
    returns an empty `items` list. The early-return at builds.py:229-230
    must leave the status unchanged.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _update_progress

    detector = Detector(
        name="tds-up-nopod",
        display_name="tds-up-nopod",
        git_url="https://github.com/x/snopod.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-up-nopod",
        status=DetectorBuildStatus.PENDING,
    )
    db_session.add(build)
    await db_session.commit()

    with _patched_update_progress_pods([]):
        await _update_progress(db_session, build, MagicMock())
    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.PENDING


@pytest.mark.asyncio
async def test_update_progress_validate_finished_sets_building(db_session, seed_user):
    """When the `validate` init container is finished, the build has cleared
    clone + validate stages and is currently executing `build`. builds.py:234.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _update_progress

    detector = Detector(
        name="tds-up-val",
        display_name="tds-up-val",
        git_url="https://github.com/x/sval.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-up-val",
        status=DetectorBuildStatus.VALIDATING,
    )
    db_session.add(build)
    await db_session.commit()

    pod = _make_build_pod(finished_init_names=["clone", "validate"])
    with _patched_update_progress_pods([pod]):
        await _update_progress(db_session, build, MagicMock())
    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.BUILDING


@pytest.mark.asyncio
async def test_update_progress_clone_finished_sets_validating(db_session, seed_user):
    """When only `clone` is finished, the build is currently executing
    `validate`. builds.py:236.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _update_progress

    detector = Detector(
        name="tds-up-clone",
        display_name="tds-up-clone",
        git_url="https://github.com/x/sclone.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-up-clone",
        status=DetectorBuildStatus.CLONING,
    )
    db_session.add(build)
    await db_session.commit()

    pod = _make_build_pod(finished_init_names=["clone"])
    with _patched_update_progress_pods([pod]):
        await _update_progress(db_session, build, MagicMock())
    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.VALIDATING


@pytest.mark.asyncio
async def test_update_progress_no_init_finished_sets_cloning(db_session, seed_user):
    """When no init container has finished yet, the build is in the clone
    stage. builds.py:238-239 fallback.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _update_progress

    detector = Detector(
        name="tds-up-none",
        display_name="tds-up-none",
        git_url="https://github.com/x/snone.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-up-none",
        status=DetectorBuildStatus.PENDING,
    )
    db_session.add(build)
    await db_session.commit()

    pod = _make_build_pod(finished_init_names=[])
    with _patched_update_progress_pods([pod]):
        await _update_progress(db_session, build, MagicMock())
    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.CLONING


# ---------------------------------------------------------------------------
# `_cleanup_build_secret` failure-metric path (builds.py:272-275).
# Same diagnostic-error / silent-swallow shape as `_cleanup_job_secret` for
# vcjobs (`test_cleanup_non_404_increments_metric` in test_reconciler_jobs_cleanup_secret.py).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_build_secret_non_404_increments_metric():
    """`_cleanup_build_secret` swallows 404 (Volcano GC won the race) silently,
    but a non-404 ApiException must bump `BACKEND_ERRORS{stage="k8s_cleanup"}`
    and log a warning — without re-raising so the failing-state commit
    upstream stays terminal.
    """
    from uuid import uuid4

    from app.metrics import BACKEND_ERRORS
    from app.reconciler.builds import _cleanup_build_secret
    from kubernetes.client import ApiException
    from prometheus_client import REGISTRY

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "k8s_cleanup"}
            )
            or 0.0
        )

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            raise ApiException(status=500, reason="server error")

    before = _get_metric()
    with patch("app.reconciler.builds.core_v1", return_value=_CoreStub()):
        await _cleanup_build_secret(uuid4())
    # +1 from the 500 path; 404 would be silent.
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="k8s_cleanup") is not None


@pytest.mark.asyncio
async def test_cleanup_build_secret_404_is_silent():
    """404 means the Secret was already GC'd — the cleanup is a no-op and
    must NOT bump the cleanup metric (otherwise normal reconcile cycles
    would inflate the BACKEND_ERRORS counter).
    """
    from uuid import uuid4

    from app.reconciler.builds import _cleanup_build_secret
    from kubernetes.client import ApiException
    from prometheus_client import REGISTRY

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "k8s_cleanup"}
            )
            or 0.0
        )

    class _CoreStub:
        def delete_namespaced_secret(self, *a, **kw):
            raise ApiException(status=404, reason="not found")

    before = _get_metric()
    with patch("app.reconciler.builds.core_v1", return_value=_CoreStub()):
        await _cleanup_build_secret(uuid4())
    assert _get_metric() == before


# ---------------------------------------------------------------------------
# `_extract_failure_reason` (builds.py:243-262) — pure helper that maps
# build pod state to a typed failure-reason string consumed by
# `_handle_failed` → `_fail_build_with_notify`. Five reasons emitted.
# ---------------------------------------------------------------------------


def _make_build_pod_with_statuses(init_statuses=None, container_statuses=None):
    """Build a minimal V1Pod-shaped stub for `_extract_failure_reason`."""

    class _Pod:
        class _Meta:
            name = "build-pod-xxx"

        metadata = _Meta()

        class _St:
            pass

        status = _St()

    p = _Pod()
    p.status.init_container_statuses = init_statuses
    p.status.container_statuses = container_statuses
    return p


def _patched_extract_reason_core(pod=None, raise_api=False):
    """Patch `app.reconciler.builds.core_v1` for `_extract_failure_reason`."""
    from kubernetes.client import ApiException

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            if raise_api:
                raise ApiException(status=500, reason="server error")

            class _R:
                items = [pod] if pod is not None else []  # stub class

            return _R()

    return patch("app.reconciler.builds.core_v1", return_value=_CoreStub())


@pytest.mark.asyncio
async def test_extract_failure_reason_pod_missing(db_session, seed_user):
    """No pod (build Job created but never produced a pod) → `pod_missing`.
    Covers builds.py:251-252.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _extract_failure_reason

    detector = Detector(
        name="tds-efr-nopod",
        display_name="tds-efr-nopod",
        git_url="https://github.com/x/sefrnp.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-efr-nopod",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    with _patched_extract_reason_core(pod=None):
        reason = await _extract_failure_reason(build)
    assert reason == "pod_missing"


@pytest.mark.asyncio
async def test_extract_failure_reason_init_container_failure(db_session, seed_user):
    """A failed init container (clone / validate) reports
    `{name}_failed: exit={code}` so the user sees which pipeline stage
    crashed. Covers builds.py:254-256.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _extract_failure_reason

    detector = Detector(
        name="tds-efr-init",
        display_name="tds-efr-init",
        git_url="https://github.com/x/sefri.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-efr-init",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    class _Term:
        exit_code = 7

    class _State:
        terminated = _Term()

    class _IC:
        name = "clone"
        state = _State()

    pod = _make_build_pod_with_statuses(init_statuses=[_IC()])
    with _patched_extract_reason_core(pod=pod):
        reason = await _extract_failure_reason(build)
    assert reason == "clone_failed: exit=7"


@pytest.mark.asyncio
async def test_extract_failure_reason_container_failure(db_session, seed_user):
    """A failed (non-init) container — e.g. BuildKit `build` step itself
    crashed mid-build — reports `{name}_failed: exit={code}`.
    Covers builds.py:257-259.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _extract_failure_reason

    detector = Detector(
        name="tds-efr-cont",
        display_name="tds-efr-cont",
        git_url="https://github.com/x/sefrc.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-efr-cont",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    class _Term:
        exit_code = 1

    class _State:
        terminated = _Term()

    class _CS:
        name = "buildkit"
        state = _State()

    pod = _make_build_pod_with_statuses(init_statuses=[], container_statuses=[_CS()])
    with _patched_extract_reason_core(pod=pod):
        reason = await _extract_failure_reason(build)
    assert reason == "buildkit_failed: exit=1"


@pytest.mark.asyncio
async def test_extract_failure_reason_unknown_failure_fallback(db_session, seed_user):
    """No init / container failure (e.g. pod evicted before any container
    terminated cleanly) → `unknown_failure`. Covers builds.py:260.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _extract_failure_reason

    detector = Detector(
        name="tds-efr-unk",
        display_name="tds-efr-unk",
        git_url="https://github.com/x/sefru.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-efr-unk",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    pod = _make_build_pod_with_statuses(init_statuses=None, container_statuses=None)
    with _patched_extract_reason_core(pod=pod):
        reason = await _extract_failure_reason(build)
    assert reason == "unknown_failure"


@pytest.mark.asyncio
async def test_extract_failure_reason_k8s_api_error(db_session, seed_user):
    """ApiException on the pods-list call must be swallowed and return
    `k8s_api_error` so the FAILED transition still carries a typed reason.
    Covers builds.py:261-262.
    """
    from app.models.detector import Detector
    from app.reconciler.builds import _extract_failure_reason

    detector = Detector(
        name="tds-efr-api",
        display_name="tds-efr-api",
        git_url="https://github.com/x/sefra.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-efr-api",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    with _patched_extract_reason_core(raise_api=True):
        reason = await _extract_failure_reason(build)
    assert reason == "k8s_api_error"


@pytest.mark.asyncio
async def test_handle_failed_swallows_log_capture_failure(db_session, seed_user):
    """`_handle_failed` (builds.py:182-190) protects the log_tail capture
    so a K8s-side blip during pod-log read doesn't keep the build
    oscillating in BUILDING. Failure must bump
    `BACKEND_ERRORS{stage="log_capture_build"}` and the build must still
    flip to FAILED via `_fail_build_with_notify`.

    Symmetric to the `_handle_succeeded` log_tail protection (PR review
    EH-1, Phase 13a).
    """
    from app.metrics import BACKEND_ERRORS
    from app.models.detector import Detector
    from app.reconciler.builds import _handle_failed
    from prometheus_client import REGISTRY

    detector = Detector(
        name="tds-hf-log",
        display_name="tds-hf-log",
        git_url="https://github.com/x/shflog.git",
        owner_id=seed_user.id,
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=seed_user.id,
        k8s_job_name="build-tds-hf-log",
        status=DetectorBuildStatus.BUILDING,
    )
    db_session.add(build)
    await db_session.commit()

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "log_capture_build"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        # Stub _extract_failure_reason to return a typed reason without
        # needing core_v1; the test is about log-capture failure only.
        patch(
            "app.reconciler.builds._extract_failure_reason",
            new=AsyncMock(return_value="detector_failed"),
        ),
        patch(
            "app.reconciler.builds._capture_log_tail",
            new=AsyncMock(side_effect=RuntimeError("pod log read 500")),
        ),
        # _fail_build_with_notify reads owner / commits + spawns notify;
        # let it run for the FAILED-transition assertion. Stub
        # `_cleanup_build_secret` so we don't need the secret API.
        patch(
            "app.reconciler.builds._cleanup_build_secret",
            new=AsyncMock(return_value=None),
        ),
    ):
        await _handle_failed(db_session, build, job=MagicMock())

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "detector_failed"
    # log_tail is None because the capture raised — the build still
    # transitioned and the metric bumped.
    assert build.log_tail is None
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="log_capture_build") is not None
