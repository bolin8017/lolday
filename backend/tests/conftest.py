import os

os.environ.setdefault(
    "FERNET_KEY", "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="
)
os.environ.setdefault("RECONCILER_ENABLED", "false")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_async_session
from app.models import Base

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
test_engine = create_async_engine(TEST_DATABASE_URL)
test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client():
    from app.main import app

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def register_user(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    return resp.json()


async def login_user(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    return resp.json()["access_token"]


async def auth_header(client: AsyncClient, email: str, password: str) -> dict:
    token = await login_user(client, email, password)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_client_user(client):
    """Authenticated AsyncClient for a 'user'-role user."""
    await register_user(client, "user@example.dev", "Password123!")
    token = await login_user(client, "user@example.dev", "Password123!")
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def auth_client_developer(client):
    """AsyncClient authenticated as a developer-role user.

    Uses direct DB update to set role (seed admin isn't set up in tests).
    """
    await register_user(client, "dev@example.dev", "DevPass123!")
    token = await login_user(client, "dev@example.dev", "DevPass123!")
    # Promote to developer via direct DB update
    from sqlalchemy import update
    from app.models import Role, User
    async with test_session_maker() as session:
        await session.execute(
            update(User)
            .where(User.email == "dev@example.dev")
            .values(role=Role.DEVELOPER)
        )
        await session.commit()
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def auth_client_admin(client):
    """AsyncClient authenticated as an admin-role user."""
    await register_user(client, "adm@example.dev", "AdmPass123!")
    token = await login_user(client, "adm@example.dev", "AdmPass123!")
    from sqlalchemy import update
    from app.models import Role, User
    async with test_session_maker() as session:
        await session.execute(
            update(User)
            .where(User.email == "adm@example.dev")
            .values(role=Role.ADMIN, is_superuser=True)
        )
        await session.commit()
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest_asyncio.fixture
async def db_session():
    async with test_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def seed_detector(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {"name": "upxelfdet", "description": "demo", "display_name": "upxelfdet"}

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)
    # Register a PAT so build can proceed
    await auth_client_developer.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_testtoken1234567890"},
    )
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    return create.json()["id"]
