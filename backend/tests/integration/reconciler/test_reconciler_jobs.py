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
        patch("app.reconciler.jobs.volcano_v1alpha1", return_value=_VolcanoStub()),
        patch("app.reconciler.jobs.core_v1", return_value=_CoreStub()),
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
    monkeypatch.setattr("app.reconciler.jobs.MlflowClient", lambda *a, **kw: stub)
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
async def test_reconcile_job_returns_early_when_no_k8s_job_name(
    db_session, seed_job
) -> None:
    """`reconcile_job` is a no-op on jobs that haven't been dispatched yet
    (status=queued_backend, no vcjob created). The L83-84 guard prevents the
    reconciler from calling `get_namespaced_custom_object(name=None)` which
    would 400 against the Volcano API. The guard short-circuits before any
    K8s call, so the test stubs nothing — a stubbed K8s call would otherwise
    surface as `_VolcanoStub` not being installed."""
    j = await seed_job(status=JobStatus.QUEUED_BACKEND)
    j.k8s_job_name = None
    await db_session.commit()

    pre_status = j.status
    # No _patched_k8s wrapper — if the guard fails to short-circuit, the
    # real Volcano API call will raise a clear "no kubeconfig" error which
    # the test would surface.
    await reconcile_job(db_session, j)
    await db_session.refresh(j)
    # No state change — the early return left the job untouched.
    assert j.status == pre_status


def test_job_timed_out_invariant_violation_raises() -> None:
    """`_job_timed_out` is private to the reconciler's events loop, which
    pre-checks `j.started_at is not None` at L109. If a future refactor
    drops that gate, the helper's defensive guard (L170-173) surfaces a
    RuntimeError with the job id — pin that behaviour so the contract is
    explicit in tests, not implicit in code review."""
    from unittest.mock import MagicMock

    from app.reconciler.jobs import _job_timed_out

    job = MagicMock()
    job.id = uuid.uuid4()
    job.started_at = None

    with pytest.raises(RuntimeError, match="caller invariant violated"):
        _job_timed_out(job, vjob={})


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


@pytest.mark.asyncio
async def test_reconcile_job_timeout_swallows_volcano_delete_500(
    db_session, seed_job, monkeypatch
):
    """Volcano delete on timeout that returns a non-404 ApiException must
    NOT propagate — it bumps `BACKEND_ERRORS{stage="k8s_cleanup"}` and the
    job still transitions to TIMEOUT.

    The 404-case (Volcano GC won the race) is the silent-swallow side of
    the same except block and was already implicitly covered by the
    happy-path test_reconcile_job_timeout. This pins the diagnostic-error
    side so a regression that re-raises the 500 (or drops the metric bump)
    flips this test red.
    """
    from app.config import settings
    from app.metrics import BACKEND_ERRORS
    from kubernetes.client import ApiException
    from prometheus_client import REGISTRY

    monkeypatch.setattr(settings, "JOB_ACTIVE_DEADLINE_TRAIN_SECONDS", 1)
    j = await seed_job(
        status=JobStatus.RUNNING,
        started_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    vjob = {
        "apiVersion": "batch.volcano.sh/v1alpha1",
        "kind": "Job",
        "metadata": {"name": "job-xxx"},
        "status": {"state": {"phase": "Running"}},
    }

    class _VolcanoStub:
        def get_namespaced_custom_object(self, *a, **kw):
            return vjob

        def delete_namespaced_custom_object(self, *a, **kw):
            raise ApiException(status=500, reason="server error")

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            class _R:
                items: list = []  # noqa: RUF012  # stub class

            return _R()

        def read_namespaced_pod_log(self, **kw):
            return ""

        def delete_namespaced_secret(self, *a, **kw):
            pass

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "k8s_cleanup"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        patch("app.reconciler.jobs.volcano_v1alpha1", return_value=_VolcanoStub()),
        patch("app.reconciler.jobs.core_v1", return_value=_CoreStub()),
    ):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.TIMEOUT
    assert j.failure_reason == "detector_timeout"
    # +1 from the volcano delete 500 inside the timeout block (the secret
    # delete in _cleanup_job_secret is a no-op here so it does not bump).
    assert _get_metric() >= before + 1.0
    # Sanity: BACKEND_ERRORS labelset matches what app/metrics declares.
    assert BACKEND_ERRORS.labels(stage="k8s_cleanup") is not None


@pytest.mark.asyncio
async def test_reconcile_job_succeeded_swallows_summary_projection_failure(
    db_session, seed_job, mlflow_stub
):
    """`_project_summary_metrics` is an opportunistic read-model refresh that
    runs AFTER the terminal-status commit (jobs.py:261). A failure inside
    must NOT roll back the SUCCEEDED transition — the except at 268-272
    bumps `BACKEND_ERRORS{stage="summary_projection"}` and continues.

    Pins the diagnostic-error side of the projection safety net so any
    regression that re-raises (or drops the metric bump) flips this test red.
    """
    from app.metrics import BACKEND_ERRORS
    from prometheus_client import REGISTRY

    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "summary_projection"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        _patched_k8s(pod_phase=None, job_succeeded=1, job_failed=None),
        patch(
            "app.reconciler.jobs._project_summary_metrics",
            new=AsyncMock(side_effect=RuntimeError("projection blew up")),
        ),
    ):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    # Terminal transition completed despite the projection failure.
    assert j.status == JobStatus.SUCCEEDED
    assert j.finished_at is not None
    assert _get_metric() >= before + 1.0
    # Sanity: BACKEND_ERRORS labelset matches what app/metrics declares.
    assert BACKEND_ERRORS.labels(stage="summary_projection") is not None


@pytest.mark.asyncio
async def test_reconcile_job_predict_swallows_prediction_summary_failure(
    db_session, seed_job, mlflow_stub
):
    """The PREDICT-only `_project_prediction_summary` block (jobs.py 274-282)
    is a sibling safety net to summary_projection — a failure inside must
    bump `BACKEND_ERRORS{stage="prediction_summary_projection"}` and leave
    the job at SUCCEEDED. _project_summary_metrics is left intact here so
    the test isolates the prediction branch.
    """
    from app.metrics import BACKEND_ERRORS
    from prometheus_client import REGISTRY

    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.PREDICT)

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total",
                {"stage": "prediction_summary_projection"},
            )
            or 0.0
        )

    before = _get_metric()
    with (
        _patched_k8s(pod_phase=None, job_succeeded=1, job_failed=None),
        patch(
            "app.reconciler.jobs._project_prediction_summary",
            new=AsyncMock(side_effect=RuntimeError("predict projection blew up")),
        ),
    ):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.SUCCEEDED
    assert j.finished_at is not None
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="prediction_summary_projection") is not None


@pytest.mark.asyncio
async def test_reconcile_job_succeeded_swallows_model_registration_failure(
    db_session, seed_job, mlflow_stub
):
    """TRAIN-success path calls `_register_model_from_job` (jobs.py 252-256).
    Failure inside must bump `BACKEND_ERRORS{stage="model_registration"}` and
    leave the job at SUCCEEDED — the terminal transition is committed
    AFTER this try/except (jobs.py:261), but the safety net is what keeps
    the controller from re-entering the same FAILED path on next iteration
    if MLflow happens to be unreachable.
    """
    from app.metrics import BACKEND_ERRORS
    from prometheus_client import REGISTRY

    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "model_registration"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        _patched_k8s(pod_phase=None, job_succeeded=1, job_failed=None),
        patch(
            "app.reconciler.jobs._register_model_from_job",
            new=AsyncMock(side_effect=RuntimeError("mlflow create model 500")),
        ),
    ):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.SUCCEEDED
    assert j.finished_at is not None
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="model_registration") is not None


@pytest.mark.asyncio
async def test_reconcile_job_failed_swallows_summary_projection_failure(
    db_session, seed_job
):
    """The FAILED path also projects early-stage metrics (jobs.py 426-432)
    so the UI summary card surfaces what's available even when the job
    didn't finish cleanly. A projection failure must bump
    `BACKEND_ERRORS{stage="summary_projection"}` (sibling to the SUCCEEDED
    path) and the job must still transition to FAILED.

    Sibling to `test_reconcile_job_succeeded_swallows_summary_projection_failure`
    — same stage label, different caller (`_handle_job_failed` vs.
    `_handle_job_succeeded`).
    """
    from app.metrics import BACKEND_ERRORS
    from prometheus_client import REGISTRY

    j = await seed_job(status=JobStatus.RUNNING, job_type=JobType.TRAIN)

    def _get_metric() -> float:
        return (
            REGISTRY.get_sample_value(
                "lolday_backend_errors_total", {"stage": "summary_projection"}
            )
            or 0.0
        )

    before = _get_metric()
    with (
        _patched_k8s(pod_phase=None, job_succeeded=None, job_failed=1, exit_code=1),
        patch(
            "app.reconciler.jobs._project_summary_metrics",
            new=AsyncMock(side_effect=RuntimeError("projection blew up")),
        ),
    ):
        await reconcile_job(db_session, j)
    await db_session.refresh(j)
    assert j.status == JobStatus.FAILED
    assert j.failure_reason == "detector_exit_nonzero"
    assert j.finished_at is not None
    assert _get_metric() >= before + 1.0
    assert BACKEND_ERRORS.labels(stage="summary_projection") is not None


# ---------------------------------------------------------------------------
# `_extract_job_failure_reason` branch coverage (jobs.py 438-468).
#
# Direct unit tests on the helper rather than full end-to-end via
# `reconcile_job`. The helper is pure — it reads pod state from
# `core_v1().list_namespaced_pod(...)` and returns a reason string — so
# stubbing the K8s call is sufficient. The end-to-end happy path is
# already covered by `test_reconcile_job_marks_failed` (detector_exit_nonzero)
# and `test_reconcile_job_marks_oom` (detector_oom).
# ---------------------------------------------------------------------------


def _make_pod(
    init_container_statuses=None,
    container_statuses=None,
):
    """Build a minimal V1Pod-shaped stub for `_extract_job_failure_reason`."""

    class _Pod:
        class _Meta:
            name = "pod-xxx"

        metadata = _Meta()

        class _St:
            pass

        status = _St()

    p = _Pod()
    p.status.init_container_statuses = init_container_statuses
    p.status.container_statuses = container_statuses
    return p


def _patched_failure_reason_core(pod=None, raise_api=False):
    """Patch `app.reconciler.jobs.core_v1` so its `list_namespaced_pod`
    returns the supplied pod list (or raises ApiException when `raise_api`).
    """
    from kubernetes.client import ApiException

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            if raise_api:
                raise ApiException(status=500, reason="server error")

            class _R:
                items = [pod] if pod is not None else []  # stub class

            return _R()

    return patch("app.reconciler.jobs.core_v1", return_value=_CoreStub())


@pytest.mark.asyncio
async def test_extract_failure_reason_k8s_api_error_returns_reason(seed_job):
    """ApiException on the pods-list call must be swallowed and return
    `"k8s_api_error"` so the FAILED transition still carries a typed reason.
    Covers jobs.py 445-446.
    """
    from app.reconciler.jobs import _extract_job_failure_reason

    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_failure_reason_core(raise_api=True):
        reason = await _extract_job_failure_reason(j)
    assert reason == "k8s_api_error"


@pytest.mark.asyncio
async def test_extract_failure_reason_pod_missing_returns_reason(seed_job):
    """Empty pods list (Volcano GC raced the pod) returns `"pod_missing"`.
    Covers jobs.py 447-448.
    """
    from app.reconciler.jobs import _extract_job_failure_reason

    j = await seed_job(status=JobStatus.RUNNING)
    with _patched_failure_reason_core(pod=None):
        reason = await _extract_job_failure_reason(j)
    assert reason == "pod_missing"


@pytest.mark.asyncio
async def test_extract_failure_reason_model_fetcher_init_failure(seed_job):
    """A `model-fetcher` init container failing returns the dedicated
    `"source_model_not_found"` reason (more actionable than generic init).
    Covers jobs.py 457-458.
    """
    from app.reconciler.jobs import _extract_job_failure_reason

    j = await seed_job(status=JobStatus.RUNNING)

    class _Term:
        exit_code = 1

    class _State:
        terminated = _Term()

    class _IC:
        name = "model-fetcher"
        state = _State()

    pod = _make_pod(init_container_statuses=[_IC()])
    with _patched_failure_reason_core(pod=pod):
        reason = await _extract_job_failure_reason(j)
    assert reason == "source_model_not_found"


@pytest.mark.asyncio
async def test_extract_failure_reason_other_init_failure(seed_job):
    """A non-`model-fetcher` init container failing returns
    `"init_{name}_failed"`. Covers jobs.py 459.
    """
    from app.reconciler.jobs import _extract_job_failure_reason

    j = await seed_job(status=JobStatus.RUNNING)

    class _Term:
        exit_code = 2

    class _State:
        terminated = _Term()

    class _IC:
        name = "secrets-fetcher"
        state = _State()

    pod = _make_pod(init_container_statuses=[_IC()])
    with _patched_failure_reason_core(pod=pod):
        reason = await _extract_job_failure_reason(j)
    assert reason == "init_secrets-fetcher_failed"


@pytest.mark.asyncio
async def test_extract_failure_reason_unknown_failure_fallback(seed_job):
    """No init-container failure AND no terminated detector container falls
    back to `"unknown_failure"` (e.g. node-evicted pod). Covers jobs.py 468.
    """
    from app.reconciler.jobs import _extract_job_failure_reason

    j = await seed_job(status=JobStatus.RUNNING)
    # init_container_statuses=None → loop doesn't execute the if branch.
    # container_statuses=None → loop doesn't execute either.
    pod = _make_pod(init_container_statuses=None, container_statuses=None)
    with _patched_failure_reason_core(pod=pod):
        reason = await _extract_job_failure_reason(j)
    assert reason == "unknown_failure"
