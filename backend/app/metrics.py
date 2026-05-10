"""Custom Prometheus metrics for the lolday backend.

Exposed via the default REGISTRY, which prometheus-fastapi-instrumentator scrapes
for `/metrics`. Keep this module free of runtime imports other than prometheus_client
so any other module can import it without triggering circular-import edges.
"""

from prometheus_client import Counter, Gauge

BACKEND_ERRORS = Counter(
    "lolday_backend_errors_total",
    "Uncaught exceptions in silent-failure-tolerant code paths, by stage.",
    ["stage"],
)

# Phase 7.5 — piggybacks on cluster_status.get_queue_depth (refreshed every
# 10s via the TTLCache path). Triggers an alert if Volcano hasn't scheduled
# a Pending job within the staleness window, which catches scheduler outages
# or webhook races that would otherwise silently hang user submissions.
#
# Name intentionally omits `_total` — that suffix is reserved by Prometheus
# convention for monotonic Counters (this is a Gauge that can drop back to 0).
VOLCANO_PENDING_STALE = Gauge(
    "lolday_volcano_pending_stale",
    "Count of Volcano Jobs in Pending phase older than a threshold. "
    "Threshold is the service constant VOLCANO_STALE_SECONDS (default 1800s).",
)

# Phase 4 — every 10s update via cluster_status.get_queue_depth(). Distinct
# from VOLCANO_PENDING_STALE (which counts only Pending older than the stale
# threshold); this Gauge is the *total* non-terminal vcjob count, tracked so
# operators can see queue growth before any single job becomes "stale".
JOBS_PENDING_TOTAL = Gauge(
    "lolday_jobs_pending_total",
    "Total non-terminal Volcano Jobs in the lolday-jobs queue (Pending + "
    "Running). Refreshed every 10s by services.cluster_status.get_queue_depth.",
)

# Phase 6 follow-up A2 (signal-collection, not feature) — counts how often an
# admin actually changes a queued job's priority via PATCH /jobs/{id}. The
# data answers "is the manual-bump UX a bottleneck worth automating?"
# Threshold guidance: if rate > 1/day for 4 consecutive weeks, draft an A2
# (auto-aging) spec. Below that, the deferred status holds.
PRIORITY_BUMP_TOTAL = Counter(
    "lolday_priority_bump_total",
    "Successful admin priority bumps on queued_backend jobs via PATCH /jobs/{id}. "
    "Increments only when the new value actually differs from the stored value "
    "(no-op patches don't count).",
)

# 議題 B (alerting redesign) — exposes gpu_signal's fail-safe state as a
# Gauge so Alertmanager can fire `GpuSignalFailSafeStuck` when Prometheus
# is unreachable for >30 min.  See
# docs/superpowers/specs/2026-05-10-alerting-redesign-design.md §6.5.
GPU_SIGNAL_FAIL_SAFE_ACTIVE = Gauge(
    "lolday_gpu_signal_fail_safe_active",
    "1 when gpu_signal cannot reach Prom (fail-safe path active), else 0.",
)
