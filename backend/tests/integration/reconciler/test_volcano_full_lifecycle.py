"""Reconciler full Volcano vcjob lifecycle: Pending → Running → Completed.

Verifies that reconcile_job correctly drives the DB Job row through the
canonical non-terminal status progression by simulating Volcano vcjob phase
transitions via a local in-process stub — no cluster, no Docker.

Walk under test:
    1. vcjob phase "Pending"  → _update_job_progress sees no pods → PREPARING
       (status unchanged; already PREPARING from seed)
    2. vcjob phase "Pending"  → _update_job_progress sees a Running pod → RUNNING
    3. vcjob phase "Completed" → _handle_job_succeeded → SUCCEEDED

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
§7.3 reconciler/jobs.py coverage map (rank 2 risk module).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from app.models.job import JobStatus, JobType
from app.reconciler import reconcile_job

# ---------------------------------------------------------------------------
# Local Volcano / core stubs (pattern mirrors test_reconciler_jobs._patched_k8s)
# ---------------------------------------------------------------------------


def _make_vcjob_stub(phase: str):
    """Return a _VolcanoStub that reports the given Volcano phase on get."""

    class _VolcanoStub:
        def get_namespaced_custom_object(self, *a, **kw):
            return {
                "apiVersion": "batch.volcano.sh/v1alpha1",
                "kind": "Job",
                "metadata": {"name": kw.get("name", "vcjob")},
                "status": {"state": {"phase": phase}},
            }

        def delete_namespaced_custom_object(self, *a, **kw):
            return {}

    return _VolcanoStub()


def _make_core_stub(*, pod_running: bool = False):
    """Return a _CoreStub whose pod list reflects the desired running state."""

    class _Pod:
        class _Meta:
            name = "pod-xxx"

        metadata = _Meta()

        class _St:
            phase = "Running" if pod_running else "Pending"
            init_container_statuses: list = []  # noqa: RUF012  # stub class
            container_statuses: list = []  # noqa: RUF012  # stub class

        status = _St()

    class _CoreStub:
        def list_namespaced_pod(self, namespace, **kw):
            class _R:
                items: list = [_Pod()] if pod_running else []  # stub class

            return _R()

        def read_namespaced_pod_log(self, **kw):
            return ""

        def delete_namespaced_secret(self, *a, **kw):
            pass

    return _CoreStub()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.fixture
async def lifecycle_job(db_session, seed_detector_version, seed_dataset, seed_user):
    """A PREPARING Job with k8s_job_name set, ready for reconcile_job calls."""
    from app.models import Job

    dv_id = await seed_detector_version(name=f"det-{uuid.uuid4().hex[:6]}")
    tr = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
    te = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
    j = Job(
        type=JobType.TRAIN,
        status=JobStatus.PREPARING,
        detector_version_id=uuid.UUID(dv_id),
        train_dataset_id=uuid.UUID(tr),
        test_dataset_id=uuid.UUID(te),
        owner_id=seed_user.id,
        resolved_config={},
        mlflow_experiment_id="42",
        mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
        idempotency_key=uuid.uuid4().hex,
        token_hash="a" * 64,
        k8s_job_name=f"vcjob-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(j)
    await db_session.commit()
    await db_session.refresh(j)
    return j


@pytest.mark.asyncio
async def test_volcano_full_lifecycle(db_session, lifecycle_job, mock_mlflow):
    """Walk a Job through PREPARING → RUNNING → SUCCEEDED via Volcano phase changes.

    Each reconcile_job tick is driven by the vcjob phase reported by the stub:

    Tick 1: phase=Pending, no pods  → _update_job_progress returns early, status unchanged (PREPARING)
    Tick 2: phase=Pending, pod Running → _update_job_progress advances to RUNNING
    Tick 3: phase=Completed            → _handle_job_succeeded → SUCCEEDED
    """
    j = lifecycle_job

    # Tick 1 — Volcano "Pending" + no pods: status stays PREPARING.
    with (
        patch(
            "app.reconciler.jobs.volcano_v1alpha1",
            return_value=_make_vcjob_stub("Pending"),
        ),
        patch(
            "app.reconciler.jobs.core_v1",
            return_value=_make_core_stub(pod_running=False),
        ),
    ):
        await reconcile_job(db_session, j, mlflow=mock_mlflow)
    await db_session.refresh(j)
    assert j.status == JobStatus.PREPARING, (
        f"Expected PREPARING after Pending+no-pods tick, got {j.status}"
    )

    # Tick 2 — Volcano "Pending" + pod Running: _update_job_progress → RUNNING.
    with (
        patch(
            "app.reconciler.jobs.volcano_v1alpha1",
            return_value=_make_vcjob_stub("Pending"),
        ),
        patch(
            "app.reconciler.jobs.core_v1",
            return_value=_make_core_stub(pod_running=True),
        ),
    ):
        await reconcile_job(db_session, j, mlflow=mock_mlflow)
    await db_session.refresh(j)
    assert j.status == JobStatus.RUNNING, (
        f"Expected RUNNING after Pending+pod-running tick, got {j.status}"
    )

    # Tick 3 — Volcano "Completed": _handle_job_succeeded → SUCCEEDED.
    with (
        patch(
            "app.reconciler.jobs.volcano_v1alpha1",
            return_value=_make_vcjob_stub("Completed"),
        ),
        patch(
            "app.reconciler.jobs.core_v1",
            return_value=_make_core_stub(pod_running=False),
        ),
    ):
        await reconcile_job(db_session, j, mlflow=mock_mlflow)
    await db_session.refresh(j)
    assert j.status == JobStatus.SUCCEEDED, (
        f"Expected SUCCEEDED after Completed tick, got {j.status}"
    )
    assert j.finished_at is not None
