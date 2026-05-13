"""Reconciler — Volcano vcjob ↔ DB job sync, build watch, orphan cleanup.

Split from a 1467-line single file in 2026-04-30; see
``docs/superpowers/plans/2026-04-30-reconciler-split.md`` for the file
structure and module responsibilities. This package's submodules:

- :mod:`app.reconciler.notify` — Discord notify helpers
- :mod:`app.reconciler.log_capture` — pod-log capture (build + job pods)
- :mod:`app.reconciler.builds` — build reconciliation orchestrator
- :mod:`app.reconciler.build_finalize` — post-scan-SUCCESS finalization
  (CVE-block + DetectorVersion promotion)
- :mod:`app.reconciler.jobs` — Volcano vcjob reconciliation
- :mod:`app.reconciler.projections` — read-model projections from job_events
- :mod:`app.reconciler.orphans` — orphan vcjob cleanup
- :mod:`app.reconciler.model_sync` — MLflow model-registry stage sync
- :mod:`app.reconciler.loop` — the main reconciler_loop driver

The names re-exported below are the public-API surface used by
``app.main`` and ``backend/tests/``. Internal helpers are intentionally
not re-exported; tests that need to patch them must reach into the
submodule (``patch("app.reconciler.<submodule>.X")``).
"""

from app.reconciler.builds import (
    IN_FLIGHT,
    _handle_failed,
    _handle_succeeded,
    _handle_timeout,
    reconcile_build,
)
from app.reconciler.jobs import (
    _handle_job_failed,
    _handle_job_succeeded,
    reconcile_job,
)
from app.reconciler.log_capture import (
    _capture_log_tail,
    _capture_pod_logs,
    _container_from_failure_reason,
)
from app.reconciler.loop import (
    ORPHAN_SCAN_EVERY_N_ITERATIONS,
    RECONCILER_WAIT_SECONDS,
    SYNC_EVERY_N_ITERATIONS,
    reconciler_loop,
)
from app.reconciler.model_sync import (
    sync_model_versions,
)
from app.reconciler.notify import (
    NotifyContext,
    _fire_job_failed_notify,
    _user_context,
)
from app.reconciler.orphans import ORPHAN_GRACE_SECONDS as ORPHAN_GRACE_SECONDS
from app.reconciler.orphans import (
    reconcile_orphan_token_secrets,
    reconcile_orphan_vcjobs,
)
from app.reconciler.projections import (
    _project_prediction_summary,
    _project_summary_metrics,
)

__all__ = [
    "IN_FLIGHT",
    "ORPHAN_GRACE_SECONDS",
    "ORPHAN_SCAN_EVERY_N_ITERATIONS",
    "RECONCILER_WAIT_SECONDS",
    "SYNC_EVERY_N_ITERATIONS",
    "NotifyContext",
    "_capture_log_tail",
    "_capture_pod_logs",
    "_container_from_failure_reason",
    "_fire_job_failed_notify",
    "_handle_failed",
    "_handle_job_failed",
    "_handle_job_succeeded",
    "_handle_succeeded",
    "_handle_timeout",
    "_project_prediction_summary",
    "_project_summary_metrics",
    "_user_context",
    "reconcile_build",
    "reconcile_job",
    "reconcile_orphan_token_secrets",
    "reconcile_orphan_vcjobs",
    "reconciler_loop",
    "sync_model_versions",
]
