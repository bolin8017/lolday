"""§10 #30 carryover — D2.3 Task 12 (audit-log durability on real Postgres).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/routers/test_audit_log.py exercises
write_audit_log on aiosqlite. This module locks the same contract on a
real Postgres 16 container via the heavy/conftest.py real_pg_session
fixture, covering the JSONB / FK / transactional-atomicity invariants
that aiosqlite cannot exercise:

- JSONB before_jsonb / after_jsonb round-trip (aiosqlite uses plain JSON
  via the with_variant binding).
- Append-only behaviour: a successful commit persists the row;
  a rolled-back transaction takes the audit row with it (single-commit
  semantics from services/audit.write_audit_log).
- Concurrent writes from two sessions land without conflict (no unique
  constraint violation; both rows visible after both commits).

The schema is created from app.models.* metadata so the test does not
depend on Alembic having run against the heavy container.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _create_schema_on_real_pg(real_pg_engine):
    """Reflect app.models metadata onto the real PG schema once per session."""
    from app.models import (
        AuditLog,  # noqa: F401 — ensures the AuditLog table is registered with Base.metadata before create_all walks it
    )
    from app.models.user import Base

    async with real_pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def _seed_user(real_pg_session: AsyncSession):
    """Insert a User row that audit_log.actor_id can reference (FK ON DELETE RESTRICT)."""
    from app.models.user import Role, User

    handle = f"u-{uuid.uuid4().hex[:8]}"
    u = User(
        id=uuid.uuid4(),
        email=f"{handle}@dev.local",
        handle=handle,
        role=Role.USER,
    )
    real_pg_session.add(u)
    await real_pg_session.flush()
    return u


@pytest.mark.asyncio
async def test_audit_log_jsonb_roundtrip(
    real_pg_session: AsyncSession, _seed_user
) -> None:
    """before/after dicts survive a commit + re-read against JSONB."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    target = uuid.uuid4()
    before = {"name": "old", "tags": ["a", "b"], "nested": {"k": 1}}
    after = {"name": "new", "tags": ["a", "b", "c"], "nested": {"k": 2}}
    await write_audit_log(
        real_pg_session,
        actor_id=_seed_user.id,
        action="test_update",
        target_type="dataset",
        target_id=target,
        before=before,
        after=after,
    )
    await real_pg_session.flush()

    row = (
        await real_pg_session.execute(
            select(AuditLog).where(AuditLog.target_id == target)
        )
    ).scalar_one()
    assert row.before_jsonb == before
    assert row.after_jsonb == after
    assert row.actor_id == _seed_user.id
    assert row.action == "test_update"
    assert row.target_type == "dataset"


@pytest.mark.asyncio
async def test_audit_log_rollback_takes_row_with_it(real_pg_engine, _seed_user) -> None:
    """Real-PG: a rolled-back transaction must not leave an audit row."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    target = uuid.uuid4()
    SessionFactory = async_sessionmaker(real_pg_engine, expire_on_commit=False)
    async with SessionFactory() as s:
        await write_audit_log(
            s,
            actor_id=_seed_user.id,
            action="test_will_rollback",
            target_type="job",
            target_id=target,
            before=None,
            after=None,
        )
        await s.rollback()

    async with SessionFactory() as s2:
        existing = (
            await s2.execute(select(AuditLog).where(AuditLog.target_id == target))
        ).scalar_one_or_none()
        assert existing is None


@pytest.mark.asyncio
async def test_audit_log_concurrent_writes_both_persist(
    real_pg_engine, _seed_user
) -> None:
    """Two sessions append simultaneously; both rows survive after both commit."""
    from app.models.audit import AuditLog
    from app.services.audit import write_audit_log

    SessionFactory = async_sessionmaker(real_pg_engine, expire_on_commit=False)
    target_a = uuid.uuid4()
    target_b = uuid.uuid4()

    async def _append(target: uuid.UUID, action: str) -> None:
        async with SessionFactory() as s:
            await write_audit_log(
                s,
                actor_id=_seed_user.id,
                action=action,
                target_type="model",
                target_id=target,
                before=None,
                after={"action_id": action},
            )
            await s.commit()

    await asyncio.gather(
        _append(target_a, "concurrent_a"),
        _append(target_b, "concurrent_b"),
    )

    async with SessionFactory() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(AuditLog.target_id.in_([target_a, target_b]))
                )
            )
            .scalars()
            .all()
        )
        actions = sorted(r.action for r in rows)
        assert actions == ["concurrent_a", "concurrent_b"]
