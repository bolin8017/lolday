"""Admin endpoint tests — SSO-based (Phase 10).

After the Cloudflare Access SSO switch, the admin gate is role=ADMIN and
authentication bypass in tests is via dependency_override rather than the
old password-bearer flow.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_list_users(auth_client_admin: AsyncClient):
    resp = await auth_client_admin.get("/api/v1/admin/users")
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert "adm@example.dev" in emails


@pytest.mark.asyncio
async def test_non_admin_cannot_list_users(auth_client_user: AsyncClient):
    resp = await auth_client_user.get("/api/v1/admin/users")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_users(client: AsyncClient):
    # No dependency_override installed; cf_access_user demands the JWT header → 401.
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_promote_user_to_developer(auth_client_admin: AsyncClient):
    # Seed a plain USER to promote
    from tests.conftest import _make_user
    from app.models import Role

    target = await _make_user("target@example.dev", role=Role.USER)
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "developer"


@pytest.mark.asyncio
async def test_non_admin_cannot_change_role(auth_client_user: AsyncClient):
    from tests.conftest import _make_user
    from app.models import Role

    target = await _make_user("target2@example.dev", role=Role.USER)
    resp = await auth_client_user.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "admin"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_self_demote(auth_client_admin: AsyncClient):
    """Last-admin safeguard: own account cannot be demoted via this endpoint."""
    me = await auth_client_admin.get("/api/v1/users/me")
    admin_id = me.json()["id"]
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"role": "user"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_nonexistent_user_returns_404(auth_client_admin: AsyncClient):
    resp = await auth_client_admin.patch(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"role": "developer"},
    )
    assert resp.status_code == 404
