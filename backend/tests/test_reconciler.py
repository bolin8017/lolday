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
        config_schema={}, status=DetectorVersionStatus.ACTIVE,
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
        config_schema={}, status=DetectorVersionStatus.ACTIVE,
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
