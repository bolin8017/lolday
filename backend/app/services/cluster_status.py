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

from cachetools import TTLCache, cached

from app.config import settings
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)

GPU_RESOURCE = "nvidia.com/gpu"
DEFAULT_QUEUE = "lolday-training"
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
    c = core_v1()
    total = 0
    for node in c.list_node().items:
        allocatable = (node.status.allocatable or {}) if node.status else {}
        total += _int_from_quantity(allocatable.get(GPU_RESOURCE))

    in_use = 0
    for pod in c.list_pod_for_all_namespaces().items:
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
    items = volcano_v1alpha1().list_namespaced_custom_object(
        group=VOLCANO_BATCH_GROUP,
        version=VOLCANO_BATCH_VERSION,
        namespace=settings.JOB_NAMESPACE,
        plural=VOLCANO_JOB_PLURAL,
    ).get("items", [])
    return [j for j in items if (j.get("spec") or {}).get("queue") == queue_name]


def _phase(job: dict) -> str | None:
    return ((job.get("status") or {}).get("state") or {}).get("phase")


@cached(_queue_cache)
def get_queue_depth(queue_name: str = DEFAULT_QUEUE) -> int:
    return sum(
        1 for j in _list_queue_jobs(queue_name)
        if _phase(j) not in _TERMINAL_PHASES
    )


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
