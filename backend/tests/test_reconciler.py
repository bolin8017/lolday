from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.detector import DetectorBuild, DetectorBuildStatus


@pytest.mark.asyncio
async def test_reconcile_succeeded_job_moves_to_scanning(db_session):
    from app.reconciler import reconcile_build
    from app.models.detector import Detector

    detector = Detector(
        name="testdet", display_name="Test", git_url="https://github.com/x/y.git",
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
        build_token="btok_x",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv:
        bv.return_value.read_namespaced_job.return_value = fake_job
        # harbor scan pending
        with patch("app.reconciler.HarborClient") as hc:
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
    from app.reconciler import reconcile_build
    from app.models.detector import Detector

    detector = Detector(
        name="testdet2", display_name="Test2", git_url="https://github.com/x/z.git",
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
        build_token="btok_y",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc, \
         patch("app.reconciler.core_v1") as cv:
        bv.return_value.read_namespaced_job.return_value = fake_job
        from app.services.harbor import ScanResult, ScanStatus
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:deadbeef")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, critical=1, high=0, medium=0, low=0)
        )
        hc.return_value.delete_artifact = AsyncMock()
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.CVE_BLOCKED
    assert build.trivy_critical == 1
    assert build.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_timeout(db_session):
    from datetime import datetime, timedelta, timezone
    from app.reconciler import reconcile_build

    build = DetectorBuild(
        detector_id=uuid4(),
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-foo-timeout",
        status=DetectorBuildStatus.BUILDING,
        build_token="btok_z",
    )
    # started_at far in the past
    build.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 0
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, patch("app.reconciler.core_v1"):
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
    from app.reconciler import reconcile_build
    from app.models.detector import Detector
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="tds1", display_name="tds1", git_url="https://github.com/x/s1.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds1", status=DetectorBuildStatus.BUILDING,
        build_token="btok_s1",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    trigger_calls = []

    async def _capture_trigger(project, repo, digest):
        trigger_calls.append((project, repo, digest))

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc:
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
async def test_reconcile_trigger_scan_failure_keeps_build_status_and_counts_metric(db_session):
    """If Harbor is unreachable or 500s, trigger_scan raises httpx.HTTPError.
    Reconciler must log + increment metric + RETURN WITHOUT flipping
    status to SCANNING, so the next reconcile tick retries cleanly
    (the old bool-returning API silently flipped status regardless).
    """
    import httpx
    from app.reconciler import reconcile_build
    from app.models.detector import Detector
    from app.services.harbor import ScanResult, ScanStatus
    from app.metrics import BACKEND_ERRORS

    detector = Detector(
        name="tds2", display_name="tds2", git_url="https://github.com/x/s2.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds2", status=DetectorBuildStatus.BUILDING,
        build_token="btok_s2",
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
            "500", request=MagicMock(), response=MagicMock(status_code=500),
        )

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc:
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
    from app.reconciler import reconcile_build
    from app.models.detector import Detector, DetectorVersion
    from app.services.harbor import ScanResult, ScanStatus
    from app.metrics import BACKEND_ERRORS
    from sqlalchemy import select

    detector = Detector(
        name="tds-err", display_name="tds-err", git_url="https://github.com/x/err.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds-err", status=DetectorBuildStatus.BUILDING,
        build_token="btok_err",
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

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc:
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
    rows = (await db_session.execute(
        select(DetectorVersion).where(DetectorVersion.detector_id == detector.id)
    )).scalars().all()
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
    from datetime import datetime, timedelta, timezone
    from app.reconciler import reconcile_build
    from app.models.detector import Detector, DetectorVersion
    from app.services.harbor import ScanResult, ScanStatus
    from app.config import settings
    from sqlalchemy import select

    detector = Detector(
        name="tds-err-to", display_name="tds-err-to",
        git_url="https://github.com/x/errto.git", owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds-err-to", status=DetectorBuildStatus.SCANNING,
        build_token="btok_err_to",
    )
    # Stuck in scan retry past the wall-clock ceiling
    build.started_at = datetime.now(timezone.utc) - timedelta(
        seconds=settings.BUILD_TIMEOUT_SECONDS + 120
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1  # Build finished; only scan is stuck
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc, \
         patch("app.reconciler.core_v1"):
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
    rows = (await db_session.execute(
        select(DetectorVersion).where(DetectorVersion.detector_id == detector.id)
    )).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_reconcile_dedup_on_existing_version_no_unbound_local(db_session):
    """Replay: a stale stuck-scanning build finishes after a newer build
    already produced the DetectorVersion row. Must mark SUCCEEDED
    without UnboundLocalError, preserve the existing row, and fire the
    build-completed notify with the existing commit SHA.
    """
    from app.reconciler import reconcile_build
    from app.models.detector import Detector, DetectorVersion, DetectorVersionStatus
    from app.services.harbor import ScanResult, ScanStatus
    from sqlalchemy import select

    detector = Detector(
        name="tds3", display_name="tds3", git_url="https://github.com/x/s3.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    existing = DetectorVersion(
        detector_id=detector.id, git_tag="v0.1.0",
        git_sha="deadbeef" * 5,
        harbor_image="harbor/tds3:v0.1.0",
        image_digest="sha256:winner",
        status=DetectorVersionStatus.ACTIVE,
    )
    db_session.add(existing)
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds3", status=DetectorBuildStatus.SCANNING,
        build_token="btok_s3",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc, \
         patch("app.reconciler.core_v1"):
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
    rows = (await db_session.execute(
        select(DetectorVersion).where(DetectorVersion.detector_id == detector.id)
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].image_digest == "sha256:winner"


@pytest.mark.asyncio
async def test_reconcile_dedup_rejects_digest_divergence(db_session):
    """Force-pushed tag: existing row's digest differs from new build's.
    Must FAIL the new build with a clear reason rather than silently
    accept the stale existing image as authoritative.
    """
    from app.reconciler import reconcile_build
    from app.models.detector import Detector, DetectorVersion, DetectorVersionStatus
    from app.services.harbor import ScanResult, ScanStatus
    from app.metrics import BACKEND_ERRORS

    detector = Detector(
        name="tds4", display_name="tds4", git_url="https://github.com/x/s4.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    existing = DetectorVersion(
        detector_id=detector.id, git_tag="v0.1.0",
        git_sha="oldsha" * 7,
        harbor_image="harbor/tds4:v0.1.0",
        image_digest="sha256:existing-version",
        status=DetectorVersionStatus.ACTIVE,
    )
    db_session.add(existing)
    build = DetectorBuild(
        detector_id=detector.id, git_tag="v0.1.0", triggered_by_id=uuid4(),
        k8s_job_name="build-tds4", status=DetectorBuildStatus.SCANNING,
        build_token="btok_s4",
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(stage="detector_version_digest_mismatch")._value.get()

    with patch("app.reconciler.batch_v1") as bv, \
         patch("app.reconciler.HarborClient") as hc, \
         patch("app.reconciler.core_v1"):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:new-digest")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert "already bound to digest" in (build.failure_reason or "")
    after = BACKEND_ERRORS.labels(stage="detector_version_digest_mismatch")._value.get()
    assert after == before + 1
