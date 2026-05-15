"""Real-Postgres test: concurrent job submissions get distinct primary
keys and FIFO submitted_at ordering. aiosqlite cannot reproduce this
because SQLite serializes writes (the integration tier silently masks
this category of bug).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
§7.3 routers/jobs.py coverage (concurrent submit race).
"""

from __future__ import annotations

import asyncio
import itertools
import uuid

import pytest
from app.models.detector import Detector, DetectorVersion
from app.models.job import Job
from app.models.user import Base, Role, User
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tests.factories.detector_factory import DetectorFactory, DetectorVersionFactory
from tests.factories.job_factory import JobFactory
from tests.factories.user_factory import UserFactory

pytestmark = pytest.mark.heavy


@pytest.fixture(scope="session", autouse=True)
def _real_pg_schema(postgres_url: str) -> None:
    """Create all model tables on the real Postgres container once per session.

    Uses a synchronous SQLAlchemy engine (psycopg2) to avoid any event-loop
    scope entanglement. Drop-all is omitted: the container is destroyed at
    session end, making DROP redundant.
    """
    sync_url = postgres_url.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    engine.dispose()


async def _seed_parents(
    engine: AsyncEngine,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a User + Detector + DetectorVersion row so they are visible to
    independently-opened connections in concurrent-submit tests.

    Explicit uuid4() calls override polyfactory's deterministic seed so
    successive calls across randomly-ordered tests never collide on PKs.

    Returns (owner_id, detector_version_id).
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Force fresh UUIDs regardless of polyfactory's random seed state.
        user: User = UserFactory.build(id=uuid.uuid4(), role=Role.DEVELOPER)
        session.add(user)
        await session.flush()

        detector: Detector = DetectorFactory.build(id=uuid.uuid4(), owner_id=user.id)
        session.add(detector)
        await session.flush()

        version: DetectorVersion = DetectorVersionFactory.build(
            id=uuid.uuid4(), detector_id=detector.id
        )
        session.add(version)
        await session.commit()

        return user.id, version.id


@pytest.mark.asyncio
async def test_concurrent_submit_assigns_distinct_primary_keys(postgres_url: str):
    """50 jobs submitted concurrently via independent connections must produce
    50 distinct primary keys.

    Each coroutine opens its own session so the concurrent flush hits asyncpg
    across parallel connections, mirroring real request concurrency. aiosqlite
    serialises all writes and cannot expose this category of bug.
    """
    engine = create_async_engine(postgres_url)
    try:
        owner_id, detector_version_id = await _seed_parents(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def submit_one() -> uuid.UUID:
            """Open a fresh session, insert a Job, capture PK, rollback."""
            j = JobFactory.queued(
                owner_id=owner_id,
                detector_version_id=detector_version_id,
            )
            async with factory() as session:
                session.add(j)
                await session.flush()
                pk: uuid.UUID = j.id
                await session.rollback()
            return pk

        ids = await asyncio.gather(*(submit_one() for _ in range(50)))
        assert len(set(ids)) == 50, "duplicate primary keys despite concurrent flush"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_submit_preserves_submitted_at_order(postgres_url: str):
    """Submitted-at ordering is monotonic under load (FIFO queue invariant).

    Adds 50 jobs in a single session flush and verifies (submitted_at, id)
    ordering is well-defined. The server_default=now() behaviour on real
    Postgres is what this test validates; aiosqlite's SQLite engine differs.
    """
    engine = create_async_engine(postgres_url)
    try:
        owner_id, detector_version_id = await _seed_parents(engine)

        n = 50
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            jobs = [
                JobFactory.queued(
                    owner_id=owner_id,
                    detector_version_id=detector_version_id,
                )
                for _ in range(n)
            ]
            for j in jobs:
                session.add(j)
            await session.flush()

            result = await session.execute(
                select(Job.id, Job.submitted_at).order_by(Job.submitted_at, Job.id)
            )
            rows = result.all()
            assert len(rows) == n

            # FIFO invariant: ordering of (submitted_at, id) is well-defined
            for prev, curr in itertools.pairwise(rows):
                assert (prev.submitted_at, prev.id) <= (curr.submitted_at, curr.id)
    finally:
        await engine.dispose()
