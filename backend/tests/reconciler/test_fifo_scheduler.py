"""Unit tests for the FIFO scheduler reconciler (Phase 6d).

TDD-first: all tests were written before fifo_scheduler.py existed.
Tests cover the 8 core scenarios from the plan:

1. Empty queue → no-op (no submit calls).
2. Single job, gpu_count=1, cluster.free_gpu=2 → submits.
3. Single job, gpu_count=2, cluster.free_gpu=1 → does NOT submit.
4. Two jobs same priority — older submitted_at submits first.
5. Two jobs different priority — higher priority submits first.
6. HEAD not-fit → halts iteration (strict FIFO, later job not tried).
7. submit raises → job stays at queued_backend, no bad state transition.
8. submit raises for HEAD → subsequent jobs NOT submitted (strict FIFO on error).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.models.job import Job, JobStatus, JobType, ResourceProfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    resource_profile: ResourceProfile = ResourceProfile.GPU1,
    priority: int = 0,
    submitted_at: datetime | None = None,
    k8s_job_name: str | None = None,
) -> Job:
    """Build an unsaved Job ORM instance with status=queued_backend."""
    if submitted_at is None:
        submitted_at = datetime.now(UTC)
    job = Job(
        type=JobType.TRAIN,
        status=JobStatus.QUEUED_BACKEND,
        detector_version_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        resource_profile=resource_profile,
        priority=priority,
        submitted_at=submitted_at,
        k8s_job_name=k8s_job_name,
    )
    return job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_dispatch():
    """AsyncMock for dispatch_job_to_volcano — records call args, returns None."""
    return AsyncMock(return_value=None)


# ---------------------------------------------------------------------------
# Test 1: empty queue → no-op
# ---------------------------------------------------------------------------


async def test_empty_queue_no_submit(db_session, mock_dispatch):
    """reconcile_fifo_queue does nothing when no jobs have status=queued_backend."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch):
        await reconcile_fifo_queue(db_session, mock_k8s)

    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: single job fits → submits
# ---------------------------------------------------------------------------


async def test_single_job_fits_submits(db_session, mock_dispatch):
    """A single queued_backend GPU1 job submits when free_gpu >= 1."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    job = _make_job(resource_profile=ResourceProfile.GPU1)
    db_session.add(job)
    await db_session.commit()

    # 2 physical GPUs, 0 in-use pods → free_gpu = 2
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=2,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    mock_dispatch.assert_called_once()
    called_job = mock_dispatch.call_args[0][1]  # second positional arg is the Job
    assert called_job.id == job.id


# ---------------------------------------------------------------------------
# Test 3: single job doesn't fit → no submit
# ---------------------------------------------------------------------------


async def test_single_job_not_fit_no_submit(db_session, mock_dispatch):
    """A GPU2 job does NOT submit when only 1 GPU is free."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    job = _make_job(resource_profile=ResourceProfile.GPU2)
    db_session.add(job)
    await db_session.commit()

    # 1 physical GPU, 0 in use → free_gpu = 1; GPU2 needs 2 → doesn't fit
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=1,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: two same-priority jobs — older submitted_at wins
# ---------------------------------------------------------------------------


async def test_same_priority_fifo_order(db_session, mock_dispatch):
    """Two queued_backend jobs at same priority: older submitted_at is submitted first."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    now = datetime.now(UTC)
    older = _make_job(
        resource_profile=ResourceProfile.GPU1, submitted_at=now - timedelta(minutes=5)
    )
    newer = _make_job(resource_profile=ResourceProfile.GPU1, submitted_at=now)
    db_session.add_all([older, newer])
    await db_session.commit()

    # Enough capacity for one GPU1 job only
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=1,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    # Only one submit should happen (free_gpu=1, each job needs 1)
    assert mock_dispatch.call_count == 1
    submitted_job = mock_dispatch.call_args[0][1]
    assert submitted_job.id == older.id


# ---------------------------------------------------------------------------
# Test 5: two different-priority jobs — higher priority first
# ---------------------------------------------------------------------------


async def test_higher_priority_submits_first(db_session, mock_dispatch):
    """Higher-priority job submits before a lower-priority older job."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    now = datetime.now(UTC)
    # older but priority=0
    low_prio = _make_job(
        resource_profile=ResourceProfile.GPU1,
        priority=0,
        submitted_at=now - timedelta(minutes=10),
    )
    # newer but priority=5
    high_prio = _make_job(
        resource_profile=ResourceProfile.GPU1, priority=5, submitted_at=now
    )
    db_session.add_all([low_prio, high_prio])
    await db_session.commit()

    # Enough capacity for one GPU1 job only
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=1,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    assert mock_dispatch.call_count == 1
    submitted_job = mock_dispatch.call_args[0][1]
    assert submitted_job.id == high_prio.id


# ---------------------------------------------------------------------------
# Test 6: HEAD not-fit halts iteration (strict FIFO — no leapfrog)
# ---------------------------------------------------------------------------


async def test_head_not_fit_halts_iteration(db_session, mock_dispatch):
    """When HEAD doesn't fit, the loop breaks — smaller later jobs are NOT submitted."""
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    now = datetime.now(UTC)
    # HEAD: GPU2 job (needs 2 GPUs) — older, higher priority → will be first
    head = _make_job(
        resource_profile=ResourceProfile.GPU2,
        priority=0,
        submitted_at=now - timedelta(minutes=5),
    )
    # Tail: GPU1 job (needs 1 GPU) — newer
    tail = _make_job(
        resource_profile=ResourceProfile.GPU1, priority=0, submitted_at=now
    )
    db_session.add_all([head, tail])
    await db_session.commit()

    # Only 1 GPU free → GPU2 HEAD doesn't fit → loop must stop, GPU1 tail NOT submitted
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch("app.reconciler.fifo_scheduler.dispatch_job_to_volcano", mock_dispatch),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=1,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    # Strict FIFO: neither job submitted
    mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: dispatch raises → job stays at queued_backend
# ---------------------------------------------------------------------------


async def test_dispatch_error_keeps_queued_backend(db_session):
    """When dispatch_job_to_volcano raises, the job's status stays queued_backend.

    Also asserts strict-FIFO-on-error: only ONE dispatch attempt is made —
    the second job in the queue must NOT be tried in the same cycle.
    """
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    now = datetime.now(UTC)
    job_a = _make_job(
        resource_profile=ResourceProfile.GPU1, submitted_at=now - timedelta(minutes=1)
    )
    job_b = _make_job(resource_profile=ResourceProfile.GPU1, submitted_at=now)
    db_session.add_all([job_a, job_b])
    await db_session.commit()

    failing_dispatch = AsyncMock(side_effect=RuntimeError("K8s API exploded"))

    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    with (
        patch(
            "app.reconciler.fifo_scheduler.dispatch_job_to_volcano", failing_dispatch
        ),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=2,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        # Should not raise — error is swallowed and job left at queued_backend
        await reconcile_fifo_queue(db_session, mock_k8s)

    # Strict FIFO on error: dispatch was attempted exactly once (for job_a HEAD),
    # then the loop broke — job_b was NOT tried.
    assert failing_dispatch.call_count == 1

    await db_session.refresh(job_a)
    await db_session.refresh(job_b)
    assert job_a.status == JobStatus.QUEUED_BACKEND
    assert job_b.status == JobStatus.QUEUED_BACKEND


# ---------------------------------------------------------------------------
# Test 8: dispatch error halts iteration (strict FIFO on error — no skip-past)
# ---------------------------------------------------------------------------


async def test_dispatch_error_halts_iteration(db_session):
    """When HEAD dispatch raises, subsequent queued jobs are NOT submitted.

    Regression test for the continue→break fix: the old `continue` would
    skip past the failed HEAD and try job B, which — after session.rollback()
    — would trigger a lazy-load on an expired ORM object (MissingGreenlet
    in async context).  `break` avoids this and preserves strict FIFO.
    """
    from app.reconciler.fifo_scheduler import reconcile_fifo_queue

    now = datetime.now(UTC)
    # job_a is HEAD (older, same priority) — its dispatch will fail
    job_a = _make_job(
        resource_profile=ResourceProfile.GPU1,
        priority=0,
        submitted_at=now - timedelta(minutes=5),
    )
    # job_b would fit if we tried it — but strict FIFO means we must NOT
    job_b = _make_job(
        resource_profile=ResourceProfile.GPU1,
        priority=0,
        submitted_at=now,
    )
    db_session.add_all([job_a, job_b])
    await db_session.commit()
    # Capture IDs before any rollback expires ORM state
    job_a_id = job_a.id
    job_b_id = job_b.id

    # Enough capacity for both jobs individually
    mock_k8s = MagicMock()
    mock_k8s.list_namespaced_pod.return_value = MagicMock(items=[])

    # Track which job IDs were passed to dispatch — read .id before the
    # rollback expires the objects (captured inside the side-effect closure).
    dispatched_ids: list = []

    def dispatch_side_effect(_session, job):  # generic mock helper
        dispatched_ids.append(job.id)  # safe: .id is in __dict__ before expire
        if job.id == job_a_id:
            raise RuntimeError("transient K8s error")
        return None  # job_b would succeed — but must never be reached

    failing_dispatch = AsyncMock(side_effect=dispatch_side_effect)

    with (
        patch(
            "app.reconciler.fifo_scheduler.dispatch_job_to_volcano", failing_dispatch
        ),
        patch(
            "app.reconciler.fifo_scheduler.settings",
            MagicMock(
                CLUSTER_PHYSICAL_GPU_COUNT=4,
                JOB_NAMESPACE="lolday",
            ),
        ),
    ):
        await reconcile_fifo_queue(db_session, mock_k8s)

    # dispatch was attempted ONCE (for job_a HEAD only); job_b was never tried
    assert failing_dispatch.call_count == 1
    assert dispatched_ids == [job_a_id]

    # Both jobs remain at queued_backend (retry next cycle)
    # Use merge to re-attach expired instances after rollback
    job_a_fresh = await db_session.get(type(job_a), job_a_id)
    job_b_fresh = await db_session.get(type(job_b), job_b_id)
    assert job_a_fresh is not None
    assert job_b_fresh is not None
    assert job_a_fresh.status == JobStatus.QUEUED_BACKEND
    assert job_b_fresh.status == JobStatus.QUEUED_BACKEND
