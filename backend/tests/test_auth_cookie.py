# backend/tests/test_auth_cookie.py
import pytest
from httpx import AsyncClient

from tests.conftest import register_user


@pytest.mark.asyncio
async def test_cookie_login_sets_httponly_cookie(client: AsyncClient):
    email = "cookie@example.com"
    password = "Str0ngP@ss!"
    await register_user(client, email, password)

    resp = await client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 204
    set_cookie = resp.headers.get("set-cookie", "")
    assert "lolday_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie


@pytest.mark.asyncio
async def test_cookie_login_rejects_bad_creds(client: AsyncClient):
    email = "cookiebad@example.com"
    password = "Str0ngP@ss!"
    await register_user(client, email, password)

    resp = await client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": "wrong"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cookie_logout_clears_cookie(client: AsyncClient):
    email = "cookiegone@example.com"
    password = "Str0ngP@ss!"
    await register_user(client, email, password)

    # Login first to obtain a cookie
    login_resp = await client.post(
        "/api/v1/auth/cookie/login",
        data={"username": email, "password": password},
    )
    assert login_resp.status_code == 204

    # Logout clears the cookie (Max-Age=0)
    resp = await client.post("/api/v1/auth/cookie/logout")
    assert resp.status_code == 204
    set_cookie = resp.headers.get("set-cookie", "")
    assert "Max-Age=0" in set_cookie or 'Max-Age="0"' in set_cookie


@pytest.mark.asyncio
async def test_bearer_login_still_works(client: AsyncClient):
    """Regression: existing Bearer flow must keep working for Phase 4 curl E2E."""
    email = "stillbearer@example.com"
    password = "Str0ngP@ss!"
    await register_user(client, email, password)

    resp = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
