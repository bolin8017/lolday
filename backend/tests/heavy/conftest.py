"""Heavy-tier fixtures: real Postgres / MLflow / MinIO containers via
testcontainers-python. Session-scoped startup; per-test isolation via
transaction rollback. Applies to backend/tests/heavy/ tree only.

Heavy tests are skipped in PR fast tier (-m 'not heavy') and run on
main push + nightly cron via backend-slow.yml (D1.6 / T34)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    # Reason: container boot is ~5s; session-scoped amortises across the heavy tier.
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    # testcontainers' get_connection_url returns the psycopg2 variant;
    # switch to asyncpg for our async SQLAlchemy engine.
    return postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://", 1
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def real_pg_engine(postgres_url: str) -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(postgres_url, future=True, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def real_pg_session(real_pg_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test transaction with rollback at teardown — keeps tests
    independent without re-creating the schema each time."""
    async with real_pg_engine.connect() as conn:
        trans = await conn.begin()
        Session = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with Session() as session:
            yield session
        await trans.rollback()


@pytest.fixture(scope="session")
def minio_container() -> Generator[MinioContainer, None, None]:
    with MinioContainer() as minio:
        yield minio


@pytest.fixture(scope="session")
def mlflow_url() -> Generator[str, None, None]:
    """Spin up a real MLflow 3.x server in a container with SQLite tracking
    and a tmpfs artifact root. Returns the base URL."""
    mlflow = (
        DockerContainer("ghcr.io/mlflow/mlflow:v3.11.1")
        .with_command(
            "mlflow server "
            "--host 0.0.0.0 "
            "--port 5000 "
            "--backend-store-uri sqlite:////tmp/mlflow.db "
            "--default-artifact-root /tmp/artifacts"
        )
        .with_exposed_ports(5000)
    )
    mlflow.start()
    # MLflow 3.x uses uvicorn; the ready signal changed from Gunicorn's
    # "Listening at" (2.x) to uvicorn's "Application startup complete."
    wait_for_logs(mlflow, "Application startup complete.", timeout=60)
    try:
        yield f"http://{mlflow.get_container_host_ip()}:{mlflow.get_exposed_port(5000)}"
    finally:
        mlflow.stop()
