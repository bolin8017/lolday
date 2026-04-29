"""Phase 11e one-shot backfill — populate summary_metrics for terminal jobs.

Idempotent. Run after phase 11e backend deploy if the operator wants the
audit-trail jobs from phase 11d to display final metrics.

Usage (from inside the backend pod):
    uv run python /app/scripts/backfill-summary-metrics.py

or locally with proper env:
    cd backend && uv run python ../scripts/backfill-summary-metrics.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.db import async_session_maker
from app.models import Job
from app.models.job import NON_TERMINAL_STATUSES
from app.reconciler import _project_summary_metrics
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("backfill")


async def main() -> int:
    async with async_session_maker() as session:
        terminal_with_null = (
            (
                await session.execute(
                    select(Job.id).where(
                        ~Job.status.in_(NON_TERMINAL_STATUSES),
                        Job.summary_metrics.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    _log.info(
        "found %d terminal jobs with null summary_metrics", len(terminal_with_null)
    )
    failures = 0
    for jid in terminal_with_null:
        async with async_session_maker() as session:
            try:
                await _project_summary_metrics(session, jid)
                _log.info("projected %s", jid)
            except Exception:
                failures += 1
                _log.exception("projection failed for %s", jid)

    if failures:
        _log.warning("backfill complete with %d failures", failures)
        return 1
    _log.info("backfill complete; all rows projected")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
