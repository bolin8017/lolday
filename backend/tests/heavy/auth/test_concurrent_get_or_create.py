"""Real-Postgres test: concurrent `get_or_create_user_by_email` calls for
the same email resolve to a single User row.

aiosqlite serialises writes so its integration tier silently passes
without exercising the `IntegrityError` race-fallback at
`app/auth/cf_access.py:174-179`. Under Postgres the UNIQUE(email)
constraint raises on N-1 of the N concurrent INSERTs, and each loser
falls through to the re-query path that returns the winner's row.

Spec: this is the heavy-tier complement of the cf_access coverage
pass (#473-#478) that closed every defensive arm reachable from
aiosqlite. The race-fallback only fires under Postgres-level
isolation, hence here.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.models.user import Base, User
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.heavy


@pytest.fixture(scope="session", autouse=True)
def _real_pg_schema(postgres_url: str) -> None:
    """Create all model tables on the real Postgres container once per
    session. Mirrors the pattern in
    `tests/heavy/postgres/test_jobs_concurrent_submit.py`."""
    sync_url = postgres_url.replace("+asyncpg", "")
    engine = create_engine(sync_url)
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_get_or_create_resolves_to_single_user(postgres_url: str):
    """10 concurrent first-visit auths for the same email must produce
    exactly one User row, and all 10 callers must receive that same row.

    Each coroutine opens its own asyncpg-backed session so the INSERTs
    actually race across distinct connections — the UNIQUE(email)
    constraint raises `IntegrityError` on N-1 of them. The catch arm
    (`app/auth/cf_access.py:174-179`) rolls back and re-queries; the
    re-query is committed-visible because the "winning" caller's
    transaction has already committed by the time the loser hits the
    catch path.
    """
    from app.auth.cf_access import get_or_create_user_by_email

    engine = create_async_engine(postgres_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        racing_email = f"race-{uuid.uuid4().hex[:8]}@example.com"

        async def resolve_one() -> uuid.UUID:
            async with factory() as session:
                user = await get_or_create_user_by_email(session, racing_email)
                return user.id

        N = 10
        ids = await asyncio.gather(*(resolve_one() for _ in range(N)))

        # Every concurrent caller received the same User id.
        assert len(set(ids)) == 1, (
            f"expected all {N} callers to share one User row; got ids={set(ids)}"
        )

        # DB-side invariant: exactly one row materialised.
        async with factory() as session:
            rows = (
                (await session.execute(select(User).where(User.email == racing_email)))
                .scalars()
                .all()
            )
        assert len(rows) == 1, (
            f"expected exactly one User row for {racing_email}; got {len(rows)}"
        )
    finally:
        await engine.dispose()
