import pytest
from httpx import AsyncClient

from tests.conftest import auth_header, register_user


@pytest.mark.asyncio
async def test_register(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["role"] == "user"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient):
    await register_user(client, "dup@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "dup@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    await register_user(client, "login@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "login@example.com", "password": "Str0ngP@ss!"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await register_user(client, "wrong@example.com", "Str0ngP@ss!")
    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": "wrong@example.com", "password": "bad"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_profile(client: AsyncClient):
    await register_user(client, "me@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "me@example.com", "Str0ngP@ss!")
    resp = await client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_get_profile_unauthenticated(client: AsyncClient):
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_user_cannot_self_promote_role(client: AsyncClient):
    await register_user(client, "sneaky@example.com", "Str0ngP@ss!")
    headers = await auth_header(client, "sneaky@example.com", "Str0ngP@ss!")
    resp = await client.patch(
        "/api/v1/users/me",
        json={"role": "admin"},
        headers=headers,
    )
    assert resp.status_code == 422 or resp.json().get("role") == "user"


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
