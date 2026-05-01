"""The main reconciler loop driver.

:func:`reconciler_loop` is invoked once per backend pod from
:func:`app.main.lifespan` and runs forever (until shutdown). Each
iteration:

1. Reconciles every in-flight DetectorBuild via
   :func:`app.reconciler.builds.reconcile_build`.
2. Reconciles every non-terminal Job via
   :func:`app.reconciler.jobs.reconcile_job`.
3. Every ``SYNC_EVERY_N_ITERATIONS`` (~60s default), syncs MLflow stages via
   :func:`app.reconciler.model_sync.sync_model_versions`.
4. Every ``ORPHAN_SCAN_EVERY_N_ITERATIONS`` (~5 min default), runs orphan
   vcjob cleanup via
   :func:`app.reconciler.orphans.reconcile_orphan_vcjobs`.

Iteration failures are logged and counted to ``BACKEND_ERRORS{stage="reconciler_iteration"}``;
the loop never exits except on the supplied ``stop_event``.

The tuning constants are module-level so tests can monkeypatch them to
collapse iteration time.
"""

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.db import async_session_maker
from app.metrics import BACKEND_ERRORS
from app.models.detector import DetectorBuild
from app.models.job import NON_TERMINAL_STATUSES, Job
from app.reconciler.builds import IN_FLIGHT, reconcile_build
from app.reconciler.jobs import reconcile_job
from app.reconciler.model_sync import sync_model_versions
from app.reconciler.orphans import reconcile_orphan_vcjobs

logger = logging.getLogger(__name__)

# Loop tuning. Module-level so tests can monkeypatch to collapse iteration time.
SYNC_EVERY_N_ITERATIONS = 6
ORPHAN_SCAN_EVERY_N_ITERATIONS = 30  # ~5 min at the default 10s wait
RECONCILER_WAIT_SECONDS = 10


async def reconciler_loop(stop_event: asyncio.Event) -> None:
    logger.info("reconciler started (build + job)")
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        try:
            async with async_session_maker() as session:
                # Build reconcile pass
                res_builds = await session.execute(
                    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
                )
                for b in res_builds.scalars().all():
                    try:
                        await reconcile_build(session, b)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_build").inc()
                        logger.exception(
                            "reconcile_build failed", extra={"build_id": str(b.id)}
                        )

                # Job reconcile pass (Phase 4)
                res_jobs = await session.execute(
                    select(Job).where(Job.status.in_(NON_TERMINAL_STATUSES))
                )
                for j in res_jobs.scalars().all():
                    try:
                        await reconcile_job(session, j)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_job").inc()
                        logger.exception(
                            "reconcile_job failed", extra={"job_id": str(j.id)}
                        )

                # Model version sync every N iterations (~60s at default N=6)
                if iteration % SYNC_EVERY_N_ITERATIONS == 0:
                    try:
                        await sync_model_versions(session)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="sync_model_versions").inc()
                        logger.exception("sync_model_versions failed")

                # Orphan vcjob scan (~5 min at default N=30)
                if iteration % ORPHAN_SCAN_EVERY_N_ITERATIONS == 0:
                    try:
                        await reconcile_orphan_vcjobs(session)
                    except Exception:
                        BACKEND_ERRORS.labels(stage="reconcile_orphan_vcjobs").inc()
                        logger.exception("reconcile_orphan_vcjobs failed")
        except Exception:
            BACKEND_ERRORS.labels(stage="reconciler_iteration").inc()
            logger.exception("reconciler iteration failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=RECONCILER_WAIT_SECONDS)
    logger.info("reconciler stopped")
