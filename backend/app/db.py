from collections.abc import AsyncGenerator

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


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
