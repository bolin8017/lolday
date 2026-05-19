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

# H-27 (security-hardening P5) — Cloudflare Access JWT verification
# failures broken out by attribution. Feeds the LoldayAuthFailureSpike
# Alertmanager rule (rate > 0.5/s for 5m). Cardinality is bounded to
# 4 values: missing_header, jwks_lookup_failed, invalid_signature,
# missing_principal_claim. Do not raise label values from the request
# (would enable cardinality blow-up via attacker-controlled errors).
AUTH_FAILURE_TOTAL = Counter(
    "lolday_auth_failure_total",
    "Cloudflare Access JWT verifications that failed, by attribution.",
    ["reason"],
)

# M-ratelimit-metric (security-hardening P5) — fixed-window limiter
# overflows (HTTP 429) attributed by prefix. Two prefixes today:
# jobs_create (POST /jobs) and builds_create (POST /detectors/{id}/builds).
# Feeds the LoldayRateLimitSpike rule (rate > 1/s for 10m).
RATE_LIMIT_HITS_TOTAL = Counter(
    "lolday_rate_limit_hits_total",
    "Rate-limit 429 responses, by prefix label.",
    ["prefix"],
)

# L-event-broker-drops (security-hardening P5) — EventBroker.publish
# discards the oldest queue entry when a subscriber's bounded Queue
# (maxsize=1000 in events_tail) is full. Unlabeled — job_id labels
# would blow up cardinality.
EVENT_BROKER_DROPS_TOTAL = Counter(
    "lolday_event_broker_drops_total",
    "EventBroker.publish drop-oldest events (subscriber queue saturated).",
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

# M-reconciler-limit (security-hardening P6) -- reconciler scan cap. Each
# iteration of reconciler_loop scans at most RECONCILER_SCAN_LIMIT non-
# terminal rows; this counter increments when the cap was hit (rows
# returned == limit), partitioned by kind (build|job). A sustained
# rate > 0 indicates the queue is growing faster than reconciliation
# progresses; the cap protects iteration latency by capping per-iter
# work. No alert rule in this phase -- P7 follow-up if rate is interesting.
RECONCILER_SCAN_TRUNCATED_TOTAL = Counter(
    "lolday_reconciler_scan_truncated_total",
    "Reconciler scan returned the cap limit -- newer rows deferred to next iteration.",
    ["kind"],
)

# Alerting redesign — exposes gpu_signal's fail-safe state as a Gauge so
# Alertmanager can fire `GpuSignalFailSafeStuck` when Prometheus is
# unreachable for >30 min.  See
# docs/superpowers/specs/2026-05-10-alerting-redesign-design.md §6.5.
GPU_SIGNAL_FAIL_SAFE_ACTIVE = Gauge(
    "lolday_gpu_signal_fail_safe_active",
    "1 when gpu_signal cannot reach Prom (fail-safe path active), else 0.",
)

# 2026-05-17 discord audit follow-up #4 — outcome of every notify_*
# webhook POST attempt. Sibling to BACKEND_ERRORS{stage="discord_notify"
# | "discord_notify_dropped"} (which counts the failure-side); this
# Counter counts ALL outcomes and lets dashboards plot success rate
# (= ok / total) over time. Labels are bounded:
#   channel ∈ {events}: currently only the backend-driven Spidey
#     Service Alerts webhook (services/notify.py:post_webhook).
#     Future-proofed for additional backend webhook paths.
#   result ∈ {ok, http_error, network_error, dropped}: ok = 2xx;
#     http_error = non-2xx Discord response; network_error = httpx
#     network exception (timeout / DNS / connection); dropped =
#     _NOTIFY_SEM saturated, no HTTP attempt made.
# Cardinality: 1 channel x 4 results = 4 series. Bounded by enumeration;
# never derived from user input.
DISCORD_NOTIFY_TOTAL = Counter(
    "lolday_discord_notify_total",
    "Outcome of every Discord webhook POST attempt. result=ok counts "
    "2xx; http_error counts non-2xx Discord responses; network_error "
    "counts httpx network exceptions; dropped counts _NOTIFY_SEM "
    "saturation drops (no HTTP attempt). Sibling to BACKEND_ERRORS{"
    'stage~="discord_notify.*"}; this Counter spans success+failure so '
    "operators can plot success-rate = ok / sum(result) over time. "
    "See docs/superpowers/specs/2026-05-17-discord-notification-audit-"
    "design.md §10 follow-up #4.",
    ["channel", "result"],
)
