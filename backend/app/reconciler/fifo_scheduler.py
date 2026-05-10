"""Application-layer FIFO scheduler (Phase 6d + Phase A host-aware GPU signal).

:func:`reconcile_fifo_queue` runs every 30 s (env: FIFO_RECONCILER_PERIOD_SECONDS).
It pulls all jobs with ``status=queued_backend``, sorts them by
``(priority DESC, submitted_at ASC)`` (AWS Batch / Slurm-no-backfill model),
and submits the HEAD job to Volcano when the cluster has enough free GPUs.

Strict FIFO semantics: if HEAD doesn't fit, the loop **breaks** — later jobs
are NOT submitted even if they would fit individually.  This prevents
multi-GPU jobs from being perpetually leapfrogged by smaller jobs (the
problem that motivated Phase 6).

Free-GPU accounting (Phase A — host-aware):
- Primary source: ``gpu_signal.compute_real_gpu_state()``.  Reads DCGM
  metrics via Prometheus to detect both K8s and non-K8s GPU usage on
  server30 (a shared lab server).  ``free_count`` reflects GPUs not held
  by any process — K8s or otherwise.
- Fail-safe handling: when Prometheus is unreachable ``gpu_signal`` sets
  ``fail_safe_active=True``.  The scheduler then:
  - ``GPU_SIGNAL_FAIL_SAFE_BLOCK=true`` (default) → return 0 (fail-closed,
    no new dispatches until Prometheus recovers).
  - ``GPU_SIGNAL_FAIL_SAFE_BLOCK=false`` (escape hatch) → fall back to the
    Phase 6 K8s-only computation (pod resource limits in ``JOB_NAMESPACE``).
- Within a single reconciler cycle, each successful submission decrements
  the local ``free_gpu`` counter so we don't over-commit in one pass.

The reconciler coexists with the existing ``reconciler_loop`` (which syncs
Volcano vcjob → DB state).  The existing loop handles *in-flight* jobs
(pending/running); this scheduler handles *pre-submission* jobs
(queued_backend).  They operate on non-overlapping status values, so no
locking is needed beyond Python's asyncio single-thread semantics and the
``replicas=1`` backend deployment guarantee.

Failure handling:
- K8s dispatch errors are caught per-job; the job stays at ``queued_backend``
  and is retried on the next cycle.
- The session is rolled back per-job on dispatch failure so no partial
  status transitions persist.
- Listing or DB query failures propagate to the caller
  (:func:`_run_fifo_reconciler_forever`) which logs + counts the error.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.job import Job, JobStatus
from app.services import gpu_signal
from app.services.jobs_dispatch import dispatch_job_to_volcano

logger = logging.getLogger(__name__)

GPU_RESOURCE = "nvidia.com/gpu"


async def _compute_free_gpu_k8s_only(session: AsyncSession, k8s) -> int:
    """Phase 6 K8s-only computation.  Used when host-aware path is in
    fail-safe AND ``GPU_SIGNAL_FAIL_SAFE_BLOCK=false`` is set."""
    physical: int = settings.CLUSTER_PHYSICAL_GPU_COUNT
    pod_gpu = 0
    try:
        # Sync K8s client wrapped in ``asyncio.to_thread`` so a slow API
        # server doesn't block the asyncio loop alongside the other backend
        # tasks (request handlers, the vcjob status reconciler, etc.).
        pod_list = await asyncio.to_thread(
            k8s.list_namespaced_pod, namespace=settings.JOB_NAMESPACE
        )
        for pod in pod_list.items:
            if not pod.status or pod.status.phase not in ("Running", "Pending"):
                continue
            for container in (pod.spec.containers or []) if pod.spec else []:
                limits = (
                    (container.resources.limits or {}) if container.resources else {}
                )
                with contextlib.suppress(TypeError, ValueError):
                    pod_gpu += int(limits.get(GPU_RESOURCE, 0))
    except Exception:
        BACKEND_ERRORS.labels(stage="fifo_scheduler_pod_list").inc()
        logger.exception(
            "fifo_scheduler: failed to list pods for GPU accounting (k8s-only path)"
        )
        return 0
    return max(0, physical - pod_gpu)


async def _compute_cluster_free_gpu(
    session: AsyncSession, k8s
) -> int:  # k8s: kubernetes.client.CoreV1Api (injected in prod; mock in tests)
    """Return the number of GPUs available for new submissions.

    Uses host-aware gpu_signal as the primary source (counts both K8s and
    non-K8s GPU usage on server30).  When Prometheus is unreachable:
    - GPU_SIGNAL_FAIL_SAFE_BLOCK=true (default) → return 0 (fail-closed)
    - GPU_SIGNAL_FAIL_SAFE_BLOCK=false (escape hatch) → fall back to the
      Phase 6 K8s-only computation.

    The reconciler decrements its local ``free_gpu`` counter after each
    successful submission within the same cycle so a burst of fits in one
    pass doesn't over-commit.
    """
    state = await asyncio.to_thread(gpu_signal.compute_real_gpu_state)

    if state.fail_safe_active:
        if settings.GPU_SIGNAL_FAIL_SAFE_BLOCK:
            return 0
        return await _compute_free_gpu_k8s_only(session, k8s)

    return state.free_count


async def reconcile_fifo_queue(
    session: AsyncSession, k8s
) -> None:  # k8s: kubernetes.client.CoreV1Api
    """Submit queued_backend jobs to Volcano in strict FIFO order.

    Sorted by ``(priority DESC, submitted_at ASC)``; dispatches HEAD when
    ``free_gpu >= job.resource_profile.gpu_count``.  Breaks on first
    non-fitting HEAD (strict FIFO; no leapfrog).

    Each successful submit is committed individually.  A dispatch failure
    rolls back only that job's status mutation — the job stays at
    ``queued_backend`` and is retried on the next cycle.
    """
    free_gpu = await _compute_cluster_free_gpu(session, k8s)

    result = await session.execute(
        select(Job)
        .where(Job.status == JobStatus.QUEUED_BACKEND)
        .order_by(Job.priority.desc(), Job.submitted_at.asc())
    )
    queued_jobs = result.scalars().all()

    for job in queued_jobs:
        gpu_needed = job.resource_profile.gpu_count
        if free_gpu < gpu_needed:
            # Strict FIFO: HEAD doesn't fit → stop.  Do NOT try later jobs.
            break
        try:
            await dispatch_job_to_volcano(session, job)
            await session.commit()
            free_gpu -= gpu_needed
        except Exception:
            BACKEND_ERRORS.labels(stage="fifo_scheduler_dispatch").inc()
            logger.exception(
                "fifo_scheduler: dispatch failed for job %s — leaving at queued_backend",
                job.id,
            )
            await session.rollback()
            # Strict FIFO (spec §6.4) + avoid expired-ORM bug: after rollback,
            # SQLAlchemy expires all persistent objects in the identity map.
            # Accessing job.resource_profile.gpu_count on the next iteration
            # would trigger a lazy-load in an async context → MissingGreenlet.
            # Break instead: the failed job stays at queued_backend; the next
            # 30-second cycle retries the whole queue from the top.
            # Worst case: 30s stall for jobs behind a flaky head — acceptable
            # per spec §7 (retry-next-cycle semantics).
            break
