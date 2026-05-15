"""Verify reconciler fires Discord notify on job/build terminal transitions."""

import asyncio
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from app.models.detector import DetectorBuild, DetectorBuildStatus
from app.models.job import Job, JobStatus, JobType
from app.reconciler import (
    _handle_failed,
    _handle_job_failed,
    _handle_job_succeeded,
    _handle_succeeded,
    _handle_timeout,
    reconcile_build,
    reconcile_job,
)


@contextmanager
def _patch_notify():
    # As of the _fail_build_with_notify helper extraction, all build-failure
    # notifications funnel through builds.py (build_finalize.py's fail-closed
    # branches lazy-import the helper). Patching builds.py alone is sufficient.
    bf_mock = AsyncMock()
    with (
        patch("app.reconciler.jobs.notify_job_completed", new=AsyncMock()) as jc,
        patch("app.reconciler.notify.notify_job_failed", new=AsyncMock()) as jf,
        patch(
            "app.reconciler.build_finalize.notify_build_completed", new=AsyncMock()
        ) as bc,
        patch("app.reconciler.builds.notify_build_failed", new=bf_mock),
        patch(
            "app.reconciler.build_finalize.notify_trivy_blocked", new=AsyncMock()
        ) as tb,
    ):
        yield SimpleNamespace(
            job_completed=jc,
            job_failed=jf,
            build_completed=bc,
            build_failed=bf_mock,
            trivy_blocked=tb,
        )


@pytest.mark.asyncio
async def test_handle_job_succeeded_calls_notify_completed(
    db_session, seed_user, seed_detector_version, seed_dataset, monkeypatch
):
    stub = AsyncMock()
    stub.get_run.return_value = {
        "info": {"status": "FINISHED", "run_id": "r", "experiment_id": "exp-1"},
        "data": {"metrics": {"f1": 0.91}, "params": {}, "tags": {}},
    }
    stub.create_registered_model.return_value = {"name": "upx"}
    stub.create_model_version.return_value = {
        "name": "upx",
        "version": "1",
        "run_id": "r",
    }
    monkeypatch.setattr("app.reconciler.jobs.MlflowClient", lambda *a, **kw: stub)

    dv_id = uuid.UUID(await seed_detector_version())
    tr = uuid.UUID(await seed_dataset(name="tr"))
    te = uuid.UUID(await seed_dataset(name="te"))
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.RUNNING,
        detector_version_id=dv_id,
        train_dataset_id=tr,
        test_dataset_id=te,
        owner_id=seed_user.id,
        resolved_config={},
        mlflow_experiment_id="42",
        mlflow_run_id="run-1",
        idempotency_key="abc",
        started_at=datetime.now(UTC),
        k8s_job_name="train-xxx",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with _patch_notify() as notify:
        await _handle_job_succeeded(db_session, job)
        await asyncio.sleep(0)  # let create_task-scheduled notify run
    assert notify.job_completed.await_count == 1
    kwargs = notify.job_completed.await_args.kwargs
    assert kwargs["job_type"] == "train"
    assert kwargs["user_name"]  # resolved from seed_user
    assert "job_url" in kwargs


@pytest.mark.asyncio
async def test_handle_job_failed_calls_notify_failed(
    db_session, seed_user, seed_detector_version, seed_dataset
):
    dv_id = uuid.UUID(await seed_detector_version())
    tr = uuid.UUID(await seed_dataset(name="tr"))
    te = uuid.UUID(await seed_dataset(name="te"))
    job = Job(
        type=JobType.EVALUATE,
        status=JobStatus.RUNNING,
        detector_version_id=dv_id,
        train_dataset_id=tr,
        test_dataset_id=te,
        owner_id=seed_user.id,
        resolved_config={},
        mlflow_experiment_id="42",
        mlflow_run_id="run-f",
        idempotency_key="xyz",
        started_at=datetime.now(UTC),
        k8s_job_name="eval-yyy",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    with _patch_notify() as notify:
        await _handle_job_failed(db_session, job)
        await asyncio.sleep(0)
    assert notify.job_failed.await_count == 1
    assert notify.job_completed.await_count == 0


@pytest.mark.asyncio
async def test_handle_build_succeeded_fires_completed_on_clean_scan(
    db_session, seed_user
):
    from app.models import Detector

    det = Detector(
        name="upxelfdet",
        display_name="upx",
        git_url="https://g/u",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    build = DetectorBuild(
        detector_id=det.id,
        git_tag="v0.1.0",
        status=DetectorBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
        triggered_by_id=seed_user.id,
        k8s_job_name="bld-a",
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    # Phase 11b Task 5b: reconcile_build now fetches OCI labels for the scanned
    # artifact so it can persist the maldet manifest onto DetectorVersion. The
    # stub needs to return a valid base64-JSON label or the build fails closed
    # before the completed-notify fires.
    import base64
    from pathlib import Path

    from app.services.harbor import ScanResult, ScanStatus

    _fixture_manifest = (
        Path(__file__).parent.parent.parent / "fixtures" / "valid_maldet_manifest.json"
    ).read_text()
    _label_b64 = base64.b64encode(_fixture_manifest.encode("utf-8")).decode("ascii")

    class _StubHarbor:
        async def get_artifact_digest(self, *a, **kw):
            return "sha256:abc"

        async def get_scan(self, *a, **kw):
            return ScanResult(
                status=ScanStatus.SUCCESS, critical=0, high=0, medium=0, low=0
            )

        async def get_image_labels(self, *a, **kw):
            # Phase 11c: build images must carry the OCI revision label too
            # (set by the buildkit container) — without it the reconciler
            # fails closed before the completed notify fires (test fixture
            # parity with what runs in production).
            return {
                "io.maldet.manifest": _label_b64,
                "org.opencontainers.image.revision": "deadbeef",
            }

        async def delete_tag_or_artifact(self, *a, **kw):
            pass

    with (
        patch("app.reconciler.builds.HarborClient", return_value=_StubHarbor()),
        _patch_notify() as notify,
    ):
        await _handle_succeeded(db_session, build)
        await asyncio.sleep(0)
    assert notify.build_completed.await_count == 1
    assert notify.trivy_blocked.await_count == 0


@pytest.mark.asyncio
async def test_handle_build_succeeded_fires_trivy_blocked_on_critical_cve(
    db_session, seed_user
):
    from app.models import Detector

    det = Detector(
        name="upxelfdet2",
        display_name="upx2",
        git_url="https://g/u2",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    build = DetectorBuild(
        detector_id=det.id,
        git_tag="v0.1.0",
        status=DetectorBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
        triggered_by_id=seed_user.id,
        k8s_job_name="bld-b",
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    from app.services.harbor import ScanResult, ScanStatus

    class _StubHarbor:
        async def get_artifact_digest(self, *a, **kw):
            return "sha256:abc"

        async def get_scan(self, *a, **kw):
            return ScanResult(
                status=ScanStatus.SUCCESS, critical=5, high=12, medium=0, low=0
            )

        async def delete_tag_or_artifact(self, *a, **kw):
            pass

    with (
        patch("app.reconciler.builds.HarborClient", return_value=_StubHarbor()),
        _patch_notify() as notify,
    ):
        await _handle_succeeded(db_session, build)
        await asyncio.sleep(0)
    assert notify.trivy_blocked.await_count == 1
    assert notify.build_completed.await_count == 0


@pytest.mark.asyncio
async def test_handle_build_failed_fires_notify_build_failed(db_session, seed_user):
    from app.models import Detector

    det = Detector(
        name="upxelfdet3",
        display_name="upx3",
        git_url="https://g/u3",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    build = DetectorBuild(
        detector_id=det.id,
        git_tag="v0.1.0",
        status=DetectorBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
        triggered_by_id=seed_user.id,
        k8s_job_name="bld-c",
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    with _patch_notify() as notify:
        # job arg only used for signature symmetry by _handle_failed
        await _handle_failed(db_session, build, job=None)
        await asyncio.sleep(0)
    assert notify.build_failed.await_count == 1


# -- C3: timeout + k8s_job_missing paths ---------------------------------------


@pytest.mark.asyncio
async def test_handle_build_timeout_fires_notify_failed(
    db_session, seed_user, monkeypatch
):
    from app.models import Detector

    det = Detector(
        name="timeout-det",
        display_name="t",
        git_url="https://g/t",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    build = DetectorBuild(
        detector_id=det.id,
        git_tag="v1",
        status=DetectorBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
        triggered_by_id=seed_user.id,
        k8s_job_name="bld-to",
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    # _handle_timeout tries to delete the k8s job — stub to ignore
    from app.reconciler import (
        _handle_timeout as _ht,  # noqa: F401 (re-import after patch)
    )

    with _patch_notify() as notify:
        await _handle_timeout(db_session, build)
        await asyncio.sleep(0)
    assert notify.build_failed.await_count == 1
    kwargs = notify.build_failed.await_args.kwargs
    assert "timeout" in kwargs["failure_reason"].lower()


@pytest.mark.asyncio
async def test_reconcile_build_k8s_missing_fires_notify_failed(db_session, seed_user):
    from app.models import Detector
    from kubernetes.client.exceptions import ApiException

    det = Detector(
        name="missing-det",
        display_name="m",
        git_url="https://g/m",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    build = DetectorBuild(
        detector_id=det.id,
        git_tag="v1",
        status=DetectorBuildStatus.BUILDING,
        started_at=datetime.now(UTC),
        triggered_by_id=seed_user.id,
        k8s_job_name="bld-missing",
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    class _Stub:
        def read_namespaced_job(self, **kw):
            raise ApiException(status=404)

        def delete_namespaced_secret(self, **kw):
            pass

    with (
        patch("app.reconciler.builds.batch_v1", return_value=_Stub()),
        patch("app.reconciler.builds.core_v1", return_value=_Stub()),
        _patch_notify() as notify,
    ):
        await reconcile_build(db_session, build)
        await asyncio.sleep(0)
    assert notify.build_failed.await_count == 1


@pytest.mark.asyncio
async def test_reconcile_job_k8s_missing_fires_notify_failed(
    db_session, seed_user, seed_detector_version, seed_dataset
):
    from kubernetes.client.exceptions import ApiException

    dv_id = uuid.UUID(await seed_detector_version())
    tr = uuid.UUID(await seed_dataset(name="tr"))
    te = uuid.UUID(await seed_dataset(name="te"))
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.RUNNING,
        detector_version_id=dv_id,
        train_dataset_id=tr,
        test_dataset_id=te,
        owner_id=seed_user.id,
        resolved_config={},
        mlflow_experiment_id="42",
        mlflow_run_id="r",
        idempotency_key="k-miss",
        started_at=datetime.now(UTC),
        k8s_job_name="job-missing",
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    class _Volcano:
        def get_namespaced_custom_object(self, **kw):
            raise ApiException(status=404)

        def delete_namespaced_custom_object(self, **kw):
            pass

    class _Core:
        def list_namespaced_pod(self, **kw):
            class _R:
                items: list = []  # noqa: RUF012  # stub class

            return _R()

        def read_namespaced_pod_log(self, **kw):
            return ""

        def delete_namespaced_secret(self, **kw):
            pass

    with (
        patch("app.reconciler.jobs.volcano_v1alpha1", return_value=_Volcano()),
        patch("app.reconciler.jobs.core_v1", return_value=_Core()),
        _patch_notify() as notify,
    ):
        await reconcile_job(db_session, job)
        await asyncio.sleep(0)
    assert notify.job_failed.await_count == 1
