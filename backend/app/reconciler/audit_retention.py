"""Audit-log retention sweep.

The ``audit_log`` table (:mod:`app.models.audit`) is append-only: the writer
``services/audit.py::write_audit_log`` only inserts rows. Without a bound the
table grows monotonically. This module runs a periodic sweep from
:func:`app.reconciler.loop.reconciler_loop` that deletes rows older than
``settings.AUDIT_LOG_RETENTION_DAYS``, keeping the table size bounded.

This is the single sanctioned DELETE path for ``audit_log``; there is still
no UPDATE path. It mirrors the age-based reaper shape of
``reconcile_orphan_token_secrets`` but operates on the DB rather than K8s.

A scheduled, indexed DELETE is the mainstream-appropriate retention strategy
at this platform's scale — the table is far below the row/size break-even
where ``pg_partman``-style partitioning earns its operational cost. See
``docs/superpowers/specs/2026-06-02-audit-log-retention-design.md``.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


async def reconcile_audit_retention(session: AsyncSession) -> int:
    """Delete ``audit_log`` rows older than ``AUDIT_LOG_RETENTION_DAYS``.

    Returns the number of rows deleted (for metrics / logging). A retention
    window of ``<= 0`` disables the sweep: it returns 0 without touching the
    table, so an operator can switch retention off via config alone.
    """
    days = settings.AUDIT_LOG_RETENTION_DAYS
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    # AsyncSession.execute is typed as returning Result; a DML statement yields
    # a CursorResult at runtime, which is where .rowcount lives.
    result = cast(
        CursorResult,
        await session.execute(delete(AuditLog).where(AuditLog.ts < cutoff)),
    )
    await session.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info(
            "audit-log retention: deleted %d row(s) older than %d day(s)",
            deleted,
            days,
        )
    return deleted
