"""Audit log writer — thin wrapper that defers commit to the caller.

Caller pattern: the router has already mutated a resource and is about
to ``await session.commit()``. write_audit_log() appends an AuditLog row
to the same session so the commit flushes both in one transaction.
If the commit fails, both roll back together. There is intentionally
NO try/except inside this function — silent-failure on the audit path
is exactly the bug this module exists to close.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    actor_id: UUID,
    action: str,
    target_type: str,
    target_id: UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an audit row. Caller commits in its own transaction."""
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_jsonb=before,
        after_jsonb=after,
    )
    session.add(row)
