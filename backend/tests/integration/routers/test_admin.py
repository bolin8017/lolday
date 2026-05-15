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
async def test_service_token_cannot_access_admin(
    auth_client_service_token: AsyncClient,
):
    """Phase 12.1 regression: ``ROLE_HIERARCHY`` must map ``Role.SERVICE_TOKEN``
    so ``require_role(...)`` returns 403 — not 500 (``KeyError``) which it
    would without the entry. Machine principals are strictly less privileged
    than any human role.
    """
    resp = await auth_client_service_token.get("/api/v1/admin/users")
    assert resp.status_code == 403, (
        f"expected 403 (Insufficient permissions), got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_unauthenticated_cannot_list_users(client: AsyncClient):
    # No dependency_override installed; cf_access_user demands the JWT header → 401.
    resp = await client.get("/api/v1/admin/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_promote_user_to_developer(auth_client_admin: AsyncClient):
    # Seed a plain USER to promote
    from app.models import Role

    from tests.conftest import _make_user

    target = await _make_user("target@example.dev", role=Role.USER)
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "developer"


@pytest.mark.asyncio
async def test_non_admin_cannot_change_role(auth_client_user: AsyncClient):
    from app.models import Role

    from tests.conftest import _make_user

    target = await _make_user("target2@example.dev", role=Role.USER)
    resp = await auth_client_user.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "admin"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_demote_the_last_admin(auth_client_admin: AsyncClient):
    """Invariant: the user table must always contain ≥1 admin. Demoting the
    sole admin (including self) is rejected even though the actor is an admin."""
    me = await auth_client_admin.get("/api/v1/users/me")
    admin_id = me.json()["id"]
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"role": "user"},
    )
    assert resp.status_code == 400
    # state unchanged
    me2 = await auth_client_admin.get("/api/v1/users/me")
    assert me2.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_can_demote_self_when_another_admin_exists(
    auth_client_admin: AsyncClient,
):
    """Self-demote is legal as long as another admin remains — the invariant
    is 'zero admins is forbidden', not 'self-demote is forbidden'."""
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("coadmin@example.dev", role=Role.ADMIN)
    me = await auth_client_admin.get("/api/v1/users/me")
    admin_id = me.json()["id"]
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "developer"


@pytest.mark.asyncio
async def test_second_admin_can_demote_peer_admin(
    auth_client_admin: AsyncClient,
):
    """Ensures the guard is specifically last-admin, not 'admins cannot demote
    other admins'. (Regression guard against overzealous future refactors.)"""
    from app.models import Role

    from tests.conftest import _make_user

    peer = await _make_user("peer@example.dev", role=Role.ADMIN)
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{peer.id}",
        json={"role": "user"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


@pytest.mark.asyncio
async def test_patch_nonexistent_user_returns_404(auth_client_admin: AsyncClient):
    resp = await auth_client_admin.patch(
        "/api/v1/admin/users/00000000-0000-0000-0000-000000000000",
        json={"role": "developer"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_patch_rejects_unknown_field(auth_client_admin: AsyncClient):
    """AdminUserUpdate uses extra='forbid' — sending fields other than the
    declared ones must 422. Future-proof against a regression that relaxes
    the model to default `ignore` (which would silently drop, not reject)."""
    from app.models import Role

    from tests.conftest import _make_user

    target = await _make_user("forbid@example.dev", role=Role.USER)
    resp = await auth_client_admin.patch(
        f"/api/v1/admin/users/{target.id}",
        json={"role": "developer", "is_superuser": True},
    )
    assert resp.status_code == 422
