from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# H-26: pre-emptively size the connection pool so request bursts don't queue
# on the pool checkout. 20 base + 30 overflow = 50 per pod x 2 replicas = 100
# total - exactly Postgres default max_connections. Bumping replicas beyond 2
# demands a parallel postgresql.max_connections bump (tracked as tech debt in
# docs/architecture.md §10).
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=30,
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


# When the engine speaks SQLite (test suite via aiosqlite + the
# `SPEC_LANE_STUBS` Playwright live-stack), the default `busy_timeout` of 0
# turns every concurrent write into an immediate `OperationalError: database
# is locked`. The Playwright live-stack runs the reconciler / FIFO scheduler
# alongside HTTP request handlers and multi-context specs (e.g.
# `tests/e2e/models/transfer-and-delete.spec.ts`) trip the lock in normal
# operation. Set a 30s busy timeout so SQLite waits for an in-flight write
# instead of failing immediately. Postgres doesn't honor this PRAGMA — the
# event listener is a no-op against asyncpg.
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_busy_timeout(dbapi_connection, _connection_record) -> None:
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
