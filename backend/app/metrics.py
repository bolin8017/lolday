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
