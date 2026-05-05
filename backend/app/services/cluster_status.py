"""Cluster-level status queries for the lolday UI.

GPU allocation answers "can my job start right now?" — we sum
node-level `allocatable."nvidia.com/gpu"` and subtract the same resource
from Running pods' container limits. For `nvidia.com/gpu` the device
plugin enforces `requests == limits`, so this matches what the scheduler
sees; using limits lets us share one code path with CPU/RAM accounting
should we extend it later.

Results are memoised with a 10 s TTL cache — `/cluster/gpu-status`
is polled every 15 s per logged-in UI client, so without the cache a
handful of users would multiply list_pod_for_all_namespaces traffic by N.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from cachetools import TTLCache, cached

from app.config import settings
from app.metrics import BACKEND_ERRORS, JOBS_PENDING_TOTAL, VOLCANO_PENDING_STALE
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

GPU_RESOURCE = "nvidia.com/gpu"
DEFAULT_QUEUE = "lolday-training"
VOLCANO_STALE_SECONDS = 1800  # 30m — alert on Pending jobs older than this
_TERMINAL_PHASES = {"Completed", "Failed", "Aborted", "Terminated"}
_PENDING_PHASES = {"Pending", "Inqueue", None, ""}

_gpu_cache: TTLCache = TTLCache(maxsize=1, ttl=10)
_queue_cache: TTLCache = TTLCache(maxsize=8, ttl=10)


def _int_from_quantity(value: str | int | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@cached(_gpu_cache)
def get_gpu_allocation() -> dict:
    """GPU allocation summary. Pods are scoped to `settings.JOB_NAMESPACE`
    (Phase 7.5 RBAC narrow) — GPU workloads only run in the lolday ns via
    Volcano, so counting other namespaces was pure over-grant."""
    c = core_v1()
    total = 0
    for node in c.list_node().items:
        allocatable = (node.status.allocatable or {}) if node.status else {}
        total += _int_from_quantity(allocatable.get(GPU_RESOURCE))

    in_use = 0
    for pod in c.list_namespaced_pod(namespace=settings.JOB_NAMESPACE).items:
        if not pod.status or pod.status.phase != "Running":
            continue
        for container in (pod.spec.containers or []) if pod.spec else []:
            limits = (container.resources.limits or {}) if container.resources else {}
            in_use += _int_from_quantity(limits.get(GPU_RESOURCE))

    idle = total - in_use
    if idle < 0:
        idle = 0
    return {"total": total, "in_use": in_use, "idle": idle}


def _list_queue_jobs(queue_name: str) -> list[dict]:
    items = (
        volcano_v1alpha1()
        .list_namespaced_custom_object(
            group=VOLCANO_BATCH_GROUP,
            version=VOLCANO_BATCH_VERSION,
            namespace=settings.JOB_NAMESPACE,
            plural=VOLCANO_JOB_PLURAL,
        )
        .get("items", [])
    )
    return [j for j in items if (j.get("spec") or {}).get("queue") == queue_name]


def _phase(job: dict) -> str | None:
    return ((job.get("status") or {}).get("state") or {}).get("phase")


@cached(_queue_cache)
def get_queue_depth(queue_name: str = DEFAULT_QUEUE) -> int:
    jobs = _list_queue_jobs(queue_name)
    non_terminal = [j for j in jobs if _phase(j) not in _TERMINAL_PHASES]

    # Side-effect: refresh the stale-Pending gauge so a Prometheus alert can
    # fire if the scheduler is hung. Any failure here (malformed timestamp,
    # unexpected CR shape) must NOT propagate or the gauge would stick at its
    # last value — a frozen gauge is invisible to the alert rule, so a real
    # scheduler outage would look like "all OK" until the next clean read.
    # Instead, count what we can and let per-job errors degrade gracefully.
    try:
        cutoff = datetime.now(UTC).timestamp() - VOLCANO_STALE_SECONDS
        stale = 0
        for j in non_terminal:
            if _phase(j) not in _PENDING_PHASES:
                continue  # Running is expected-pending-turned-active; don't flag
            created = (j.get("metadata") or {}).get("creationTimestamp")
            parsed = _parse_iso8601(created)
            if parsed is not None and parsed.timestamp() < cutoff:
                stale += 1
        VOLCANO_PENDING_STALE.set(stale)
    except Exception:
        BACKEND_ERRORS.labels(stage="queue_stale_gauge").inc()
        logger.exception("stale-gauge refresh failed (queue=%s)", queue_name)

    # Phase 4 — total non-terminal vcjob count for queue-depth alerting.
    # Distinct from VOLCANO_PENDING_STALE (counts only Pending older than the
    # stale threshold). Setting outside the try/except above is intentional:
    # this number is `len(non_terminal)` which is already known and cannot
    # raise.
    JOBS_PENDING_TOTAL.set(len(non_terminal))

    return len(non_terminal)


def _parse_iso8601(s: str | None) -> datetime | None:
    """Parse the RFC3339 form k8s emits (e.g. `2026-04-21T01:00:00Z`).

    Returns `None` on any unparseable input so callers can skip the bad row
    without propagating. A single malformed `creationTimestamp` in the
    Volcano CR list must not poison the entire `get_queue_depth` call.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        BACKEND_ERRORS.labels(stage="queue_stale_parse").inc()
        logger.warning("unparseable k8s creationTimestamp: %r", s)
        return None


def get_job_queue_position(
    k8s_job_name: str,
    queue_name: str = DEFAULT_QUEUE,
) -> int | None:
    # Not cached: result is per-job and queue_name + name uniqueness would
    # require a per-(queue,name) key, which blows up the cache for little gain.
    pending = [j for j in _list_queue_jobs(queue_name) if _phase(j) in _PENDING_PHASES]
    pending.sort(key=lambda j: (j.get("metadata") or {}).get("creationTimestamp", ""))
    for idx, job in enumerate(pending, start=1):
        if (job.get("metadata") or {}).get("name") == k8s_job_name:
            return idx
    return None
