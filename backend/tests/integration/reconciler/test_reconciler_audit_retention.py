"""reconcile_audit_retention — deletes audit_log rows older than the
configured window, keeps fresh rows, and is a no-op when disabled or empty.

Mirrors the age-based reaper shape of reconcile_orphan_token_secrets, but
operates on the DB (audit_log) rather than K8s Secrets. Time is injected by
constructing each row's ``ts`` relative to now (testing.md rule #2), not by
reading the wall clock inside the function under test.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select


async def _make_actor(db_session):
    """Create the actor User the audit rows' RESTRICT FK points at."""
    from app.models import Role, User

    actor = User(
        id=uuid.uuid4(),
        email=f"audit-retention-{uuid.uuid4().hex[:8]}@example.dev",
        role=Role.USER,
        handle=f"ar-{uuid.uuid4().hex[:8]}",
        display_name="Audit Retention Actor",
    )
    db_session.add(actor)
    await db_session.commit()
    return actor


def _audit_row(actor_id: uuid.UUID, *, age_days: float):
    from app.models import AuditLog

    return AuditLog(
        id=uuid.uuid4(),
        actor_id=actor_id,
        action="admin.role_change",
        target_type="user",
        target_id=uuid.uuid4(),
        ts=datetime.now(UTC) - timedelta(days=age_days),
    )


@pytest.mark.asyncio
async def test_retention_deletes_old_keeps_fresh(db_session, monkeypatch):
    """A row just past the window is deleted; one just inside survives."""
    from app.config import settings
    from app.models import AuditLog
    from app.reconciler.audit_retention import reconcile_audit_retention

    monkeypatch.setattr(settings, "AUDIT_LOG_RETENTION_DAYS", 365)
    actor = await _make_actor(db_session)
    old = _audit_row(actor.id, age_days=366)
    fresh = _audit_row(actor.id, age_days=364)
    db_session.add_all([old, fresh])
    await db_session.commit()

    deleted = await reconcile_audit_retention(db_session)

    assert deleted == 1
    remaining = (await db_session.execute(select(AuditLog.id))).scalars().all()
    assert remaining == [fresh.id]


@pytest.mark.asyncio
async def test_retention_disabled_is_noop(db_session, monkeypatch):
    """AUDIT_LOG_RETENTION_DAYS <= 0 disables the sweep (escape hatch)."""
    from app.config import settings
    from app.models import AuditLog
    from app.reconciler.audit_retention import reconcile_audit_retention

    monkeypatch.setattr(settings, "AUDIT_LOG_RETENTION_DAYS", 0)
    actor = await _make_actor(db_session)
    ancient = _audit_row(actor.id, age_days=1000)
    db_session.add(ancient)
    await db_session.commit()

    deleted = await reconcile_audit_retention(db_session)

    assert deleted == 0
    remaining = (await db_session.execute(select(AuditLog.id))).scalars().all()
    assert remaining == [ancient.id]


@pytest.mark.asyncio
async def test_retention_empty_table_is_noop(db_session, monkeypatch):
    """No rows → returns 0 without error."""
    from app.config import settings
    from app.reconciler.audit_retention import reconcile_audit_retention

    monkeypatch.setattr(settings, "AUDIT_LOG_RETENTION_DAYS", 365)

    deleted = await reconcile_audit_retention(db_session)

    assert deleted == 0
