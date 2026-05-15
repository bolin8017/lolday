"""reconcile_build populates DetectorVersion.manifest from the Harbor OCI label.

Phase 11b Task 5b: after Harbor scan Success with 0 CVEs, the reconciler must
fetch the artifact's OCI image labels, decode ``io.maldet.manifest`` through
:func:`app.services.manifest_store.decode_manifest_label`, and store the full
DetectorManifest model_dump into ``DetectorVersion.manifest``. Missing label
and malformed-base64 both must fail-closed (no DetectorVersion row created,
build marked FAILED with a specific ``failure_reason``).

Test mocking mirrors the existing ``test_reconciler.py`` pattern:
``patch("app.reconciler.builds.HarborClient")`` + ``AsyncMock`` for per-method stubs.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.models.detector import (
    Detector,
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
)
from sqlalchemy import select

FIX = Path(__file__).parent.parent.parent / "fixtures" / "valid_maldet_manifest.json"


def _b64_manifest() -> str:
    """Base64-encode the fixture manifest for inclusion in a Labels dict."""
    raw = FIX.read_text()
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


@pytest.mark.asyncio
async def test_reconcile_populates_manifest_from_image_labels(db_session):
    """Happy path: scan Success 0 CVEs + valid manifest label → DetectorVersion
    created with manifest column populated from the decoded label."""
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="elfrfdet",
        display_name="elfrfdet",
        git_url="https://github.com/bolin8017/elfrfdet.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v2.0.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-elfrfdet-1",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    labels = {
        "io.maldet.manifest": _b64_manifest(),
        # The build-helper stamps the commit SHA into this standard OCI label;
        # the reconciler reads it back and writes it to DetectorVersion.git_sha
        # (canonical post-Phase-11c flow — no schema-POST callback any more).
        "org.opencontainers.image.revision": "abc123def456",
    }

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:deadbeef")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        hc.return_value.get_image_labels = AsyncMock(return_value=labels)
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.SUCCEEDED

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
    dv = rows[0]
    assert dv.image_digest == "sha256:deadbeef"
    # git_sha flows from the OCI revision label into both DetectorVersion
    # and DetectorBuild (the build row is also kept in sync for audit trails).
    assert dv.git_sha == "abc123def456"
    assert build.git_sha == "abc123def456"
    # The full manifest survives the model_dump(mode=json) round-trip: at least
    # the keys we care about downstream (detector name, supported resources,
    # train stage module paths) must come through intact.
    assert dv.manifest is not None
    assert dv.manifest["detector"]["name"] == "elfrfdet"
    assert dv.manifest["resources"]["supports"] == ["cpu", "gpu1", "gpu2"]
    assert dv.manifest["stages"]["train"]["trainer"].startswith("maldet.")


@pytest.mark.asyncio
async def test_reconcile_fails_when_manifest_label_missing(db_session):
    """Fail-closed: scan Success 0 CVEs but the image has no
    ``io.maldet.manifest`` label → build FAILED with
    ``failure_reason="manifest_label_missing"`` and NO DetectorVersion row.
    """
    from app.metrics import BACKEND_ERRORS
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="nolabel",
        display_name="nolabel",
        git_url="https://github.com/x/nolabel.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-nolabel",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(stage="manifest_missing")._value.get()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:nomfst")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        # Image exists but has other labels only — no io.maldet.manifest.
        hc.return_value.get_image_labels = AsyncMock(
            return_value={"org.opencontainers.image.version": "0.1.0"}
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "manifest_label_missing"
    assert build.finished_at is not None

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

    after = BACKEND_ERRORS.labels(stage="manifest_missing")._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_reconcile_fails_when_revision_label_missing(db_session):
    """C2 fix: scan Success 0 CVEs + valid manifest label, but the image is
    missing ``org.opencontainers.image.revision`` → build FAILED with
    ``failure_reason="git_sha_label_missing"``. Without this fail-close, a
    DetectorVersion would be persisted with empty ``git_sha``, breaking
    audit trails for the canonical post-Phase-11c flow.
    """
    from app.metrics import BACKEND_ERRORS
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="norev",
        display_name="norev",
        git_url="https://github.com/x/norev.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.2.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-norev",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(stage="git_sha_label_missing")._value.get()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:norev")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        # Manifest label is present and valid, but the revision label is
        # absent — i.e. the buildkit image-build forgot to stamp the SHA.
        hc.return_value.get_image_labels = AsyncMock(
            return_value={"io.maldet.manifest": _b64_manifest()}
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "git_sha_label_missing"
    assert build.finished_at is not None

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

    after = BACKEND_ERRORS.labels(stage="git_sha_label_missing")._value.get()
    assert after == before + 1


@pytest.mark.asyncio
async def test_reconcile_fails_when_manifest_label_malformed(db_session):
    """Fail-closed: ``io.maldet.manifest`` present but not valid base64 →
    ``ManifestDecodeError`` → build FAILED with
    ``failure_reason="manifest_invalid"`` and NO DetectorVersion row."""
    from app.metrics import BACKEND_ERRORS
    from app.reconciler import reconcile_build
    from app.services.harbor import ScanResult, ScanStatus

    detector = Detector(
        name="badmfst",
        display_name="badmfst",
        git_url="https://github.com/x/badmfst.git",
        owner_id=uuid4(),
    )
    db_session.add(detector)
    await db_session.commit()
    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.1.0",
        triggered_by_id=uuid4(),
        k8s_job_name="build-badmfst",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    before = BACKEND_ERRORS.labels(stage="manifest_invalid")._value.get()

    with (
        patch("app.reconciler.builds.batch_v1") as bv,
        patch("app.reconciler.builds.HarborClient") as hc,
        patch("app.reconciler.builds.core_v1"),
    ):
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:bad")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        # Obvious non-base64 content inside the label — forces ManifestDecodeError
        # on the base64 path before JSON or Pydantic are reached.
        hc.return_value.get_image_labels = AsyncMock(
            return_value={"io.maldet.manifest": "@@@ not base64 @@@"}
        )
        await reconcile_build(db_session, build)

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED
    assert build.failure_reason == "manifest_invalid"
    assert build.finished_at is not None

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

    after = BACKEND_ERRORS.labels(stage="manifest_invalid")._value.get()
    assert after == before + 1
