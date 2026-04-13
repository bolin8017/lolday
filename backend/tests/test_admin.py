import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models import Role, User
from tests.conftest import auth_header, register_user, test_session_maker


async def make_admin(email: str):
    async with test_session_maker() as session:
        await session.execute(
            update(User).where(User.email == email).values(role=Role.ADMIN)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_admin_list_users(client: AsyncClient):
    await register_user(client, "admin@example.com", "Str0ngP@ss!")
    await make_admin("admin@example.com")
    await register_user(client, "user1@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "admin@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_non_admin_cannot_list_users(client: AsyncClient):
    await register_user(client, "regular@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "regular@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/admin/users", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_users(client: AsyncClient):
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401
