import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from app.models.job import JobStatus, JobType
from app.reconciler import reconcile_job


@contextmanager
def _patched_k8s(pod_phase, job_succeeded, job_failed, exit_code=0):
    # Phase 7.3: reconcile_job reads the job via CustomObjectsApi as a Volcano
    # Job (batch.volcano.sh/v1alpha1), not a batch/v1 Job. Translate the old
    # succeeded/failed booleans into Volcano's .status.state.phase enum so
    # existing test arguments stay readable.
    if job_succeeded:
        phase = "Completed"
    elif job_failed:
        phase = "Failed"
    else:
        phase = "Running"

    vjob = {
        "apiVersion": "batch.volcano.sh/v1alpha1",
        "kind": "Job",
        "metadata": {"name": "job-xxx"},
        "status": {"state": {"phase": phase}},
    }

    class _Pod:
        class _Meta:
            name = "pod-xxx"

        metadata = _Meta()

        class _St:
            phase = pod_phase
            init_container_statuses: list = []  # noqa: RUF012  # stub class
            container_statuses = (
                [
                    type(
                        "C",
                        (),
                        {
                            "name": "detector",
                            "state": type(
                                "T",
                                (),
                                {
                                    "terminated": type(
                                        "TT", (), {"exit_code": exit_code}
                                    )()
                                },
                            )(),
                        },
                    )()
                ]
                if job_failed
                else []
            )

        status = _St()

    class _VolcanoStub:
        def get_namespaced_custom_object(self, *a, **kw):
            return vjob

        def delete_namespaced_custom_object(self, *a, **kw):
            pass

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            class _R:
                items: list = [_Pod()]  # noqa: RUF012  # stub class

            return _R()

        def read_namespaced_pod_log(self, **kw):
            return "sample log tail"

        def delete_namespaced_secret(self, *a, **kw):
            pass

    with (
        patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()),
        patch("app.reconciler.core_v1", return_value=_CoreStub()),
    ):
        yield


@pytest.fixture
async def mlflow_stub(monkeypatch):
    """Replace MLflow client used by the reconciler with an AsyncMock."""
    stub = AsyncMock()
    stub.get_run.return_value = {
        "info": {"status": "FINISHED", "run_id": "r", "experiment_id": "exp-1"},
        "data": {"metrics": {"accuracy": 0.9, "f1": 0.85}, "params": {}, "tags": {}},
    }
    stub.create_registered_model.return_value = {"name": "upxelfdet"}
    stub.create_model_version.return_value = {
        "name": "upxelfdet",
        "version": "1",
        "run_id": "r",
    }
    monkeypatch.setattr("app.reconciler.MlflowClient", lambda *a, **kw: stub)
    return stub


@pytest.fixture
async def seed_job(db_session, seed_detector_version, seed_dataset, seed_user):
    async def _seed(
        status: JobStatus = JobStatus.PENDING,
        job_type: JobType = JobType.TRAIN,
        started_at=None,
    ):
        from app.models import Job

        dv_id = await seed_detector_version(name=f"det-{uuid.uuid4().hex[:6]}")
        tr = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        te = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        j = Job(
            type=job_type,
            status=status,
            detector_version_id=uuid.UUID(dv_id),
            train_dataset_id=uuid.UUID(tr),
            test_dataset_id=uuid.UUID(te),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
            idempotency_key=uuid.uuid4().hex,
            token_hash="a" * 64,
            k8s_job_name=f"job-{job_type.value}-{uuid.uuid4().hex[:8]}",
            started_at=started_at,
        )
        db_session.add(j)
        await db_session.commit()
        await db_session.refresh(j)
        return j

    return _seed


@pytest.mark.asyncio
async def test_reconcile_job_marks_running(db_session, seed_job):
    j = await seed_job(status=JobStatus.PREPARING)
    with _patched_k8s(pod_phase="Running", job_succeeded=None, job_failed=None):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.RUNNING
    assert j.started_at is not None


@pytest.mark.asyncio
async def test_reconcile_job_marks_succeeded_and_registers_model(
    db_session, seed_job, mlflow_stub
):
    """Phase 11e: summary_metrics is sourced from job_events (events-based
    projection), not MLflow. Seed metric/confusion_matrix events and verify
    the reconciler projects them on stage_end rather than copying from the
    MLflow run.
    """
    from app.models import JobEvent

    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)

    base = datetime.now(UTC)
    db_session.add_all(
        [
            JobEvent(
                id=uuid.uuid4(),
                job_id=j.id,
                ts=base,
                kind="metric",
                payload={"name": "accuracy", "value": 0.9, "step": 0},
            ),
            JobEvent(
                id=uuid.uuid4(),
                job_id=j.id,
                ts=base,
                kind="metric",
                payload={"name": "f1", "value": 0.85, "step": 0},
            ),
        ]
    )
    await db_session.commit()

    with _patched_k8s(pod_phase=None, job_succeeded=1, job_failed=None):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.SUCCEEDED
    assert j.summary_metrics == {
        "metrics": {"accuracy": 0.9, "f1": 0.85},
        "confusion_matrix": None,
        "per_class": None,
    }
    assert j.finished_at is not None


@pytest.mark.asyncio
async def test_reconcile_job_marks_failed(db_session, seed_job):
    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_k8s(pod_phase=None, job_succeeded=None, job_failed=1, exit_code=1):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.FAILED
    assert j.failure_reason == "detector_exit_nonzero"


@pytest.mark.asyncio
async def test_reconcile_job_marks_oom(db_session, seed_job):
    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_k8s(pod_phase=None, job_succeeded=None, job_failed=1, exit_code=137):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.failure_reason == "detector_oom"


@pytest.mark.asyncio
async def test_reconcile_job_timeout(db_session, seed_job, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOB_ACTIVE_DEADLINE_TRAIN_SECONDS", 1)
    j = await seed_job(
        status=JobStatus.RUNNING,
        started_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    with _patched_k8s(pod_phase="Running", job_succeeded=None, job_failed=None):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.TIMEOUT
