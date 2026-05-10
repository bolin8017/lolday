"""Cluster-level status queries for the lolday UI.

GPU allocation is host-aware: it reads DCGM/Prometheus metrics via
``gpu_signal.compute_real_gpu_state()`` so that non-K8s GPU usage on
server30 is reflected in the free-GPU count exposed to the UI and the
Phase 6 FIFO scheduler.

Results are memoised with a 10 s TTL cache — ``/cluster/gpu-status``
is polled every 15 s per logged-in UI client, so without the cache a
handful of users would multiply Prometheus traffic by N.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from cachetools import TTLCache, cached

from app.config import settings
from app.metrics import BACKEND_ERRORS, JOBS_PENDING_TOTAL, VOLCANO_PENDING_STALE
from app.services import gpu_signal
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

DEFAULT_QUEUE = "lolday-training"
VOLCANO_STALE_SECONDS = 1800  # 30m — alert on Pending jobs older than this
_TERMINAL_PHASES = {"Completed", "Failed", "Aborted", "Terminated"}
_PENDING_PHASES = {"Pending", "Inqueue", None, ""}

_gpu_cache: TTLCache = TTLCache(maxsize=1, ttl=10)
_queue_cache: TTLCache = TTLCache(maxsize=8, ttl=10)


def _state_label(s: gpu_signal.GPUStatus) -> str:
    if s.in_use_by_k8s:
        return "lolday"
    if s.in_use_by_external:
        return "external"
    return "free"


@cached(_gpu_cache)
def get_gpu_allocation() -> dict:
    """Host-aware GPU allocation summary.

    Reads from gpu_signal (Prometheus + DCGM) so non-K8s GPU usage on
    server30 is reflected.  Returns the schema documented in
    docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md §6.4.
    """
    state = gpu_signal.compute_real_gpu_state()
    return {
        "total": state.physical_total,
        "free_count": state.free_count,
        "in_use_by_lolday": state.in_use_by_lolday_count,
        "in_use_by_external": state.in_use_by_external_count,
        "fail_safe_active": state.fail_safe_active,
        "fail_safe_reason": state.fail_safe_reason,
        "per_gpu": [
            {
                "gpu_id": s.gpu_id,
                "state": _state_label(s),
                "util_percent": s.util_percent,
                "vram_used_mb": s.vram_used_mb,
            }
            for s in state.per_gpu
        ],
    }


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
