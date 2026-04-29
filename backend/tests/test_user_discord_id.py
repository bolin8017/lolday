"""Tests for User.discord_user_id column + UserSelfUpdate schema exposure."""

import pytest
from app.models import Role

from tests.conftest import _as_user, _make_user


@pytest.mark.asyncio
async def test_service_token_user_can_read_self(client):
    """Service-token JWTs synthesize emails like
    ``service-<name>@cf-access.local``. Pydantic's ``EmailStr`` rejects
    ``.local`` as a reserved TLD (this used to surface via fastapi-users'
    ``schemas.BaseUser`` before the 2026-04-29 rewrite), so the response
    model on ``GET /users/me`` would 500 when serializing the service-token
    User row. ``UserRead.email`` must remain a plain ``str``.
    """
    email = "service-abc123@cf-access.local"
    await _make_user(email, role=Role.ADMIN)
    c = _as_user(client, email)
    r = await c.get("/api/v1/users/me")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email


@pytest.mark.asyncio
async def test_service_token_role_user_can_read_self(client):
    """Phase 12.1 regression: a row with ``role=Role.SERVICE_TOKEN`` (the
    actual machine-principal shape, not the prior test's ADMIN-impersonating
    one) must round-trip through FastAPI/Pydantic. Catches a Pydantic-side
    regression that the bare SAEnum round-trip can't — e.g. a future
    ``use_enum_values=False`` flip serialising ``"SERVICE_TOKEN"`` instead
    of ``"service_token"``.
    """
    email = "service-real@cf-access.local"
    await _make_user(email, role=Role.SERVICE_TOKEN)
    c = _as_user(client, email)
    r = await c.get("/api/v1/users/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == email
    assert body["role"] == "service_token", (
        f"role must serialise as the lowercase VALUE, got {body['role']!r}"
    )


@pytest.mark.asyncio
async def test_patch_users_me_rejects_role_smuggling(user_client):
    """Privilege escalation guard: UserSelfUpdate(extra='forbid') means PATCH
    /users/me with a `role` field must 422 — not silently drop like pydantic's
    default `ignore` would. Was covered by the deleted test_user_cannot_self_promote_role
    (fastapi-users era); reintroduced here after the Phase 10 SSO swap."""
    r = await user_client.patch("/api/v1/users/me", json={"role": "admin"})
    assert r.status_code == 422
    me = await user_client.get("/api/v1/users/me")
    assert me.json()["role"] == "user"


@pytest.mark.asyncio
async def test_new_user_has_null_discord_id(user_client):
    r = await user_client.get("/api/v1/users/me")
    assert r.status_code == 200
    body = r.json()
    assert "discord_user_id" in body
    assert body["discord_user_id"] is None


@pytest.mark.asyncio
async def test_user_can_set_discord_id_via_patch_me(user_client):
    r = await user_client.patch(
        "/api/v1/users/me", json={"discord_user_id": "987654321098765432"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["discord_user_id"] == "987654321098765432"

    me = await user_client.get("/api/v1/users/me")
    assert me.json()["discord_user_id"] == "987654321098765432"


@pytest.mark.asyncio
async def test_user_can_clear_discord_id_by_setting_null(user_client):
    await user_client.patch(
        "/api/v1/users/me", json={"discord_user_id": "987654321098765432"}
    )
    r = await user_client.patch("/api/v1/users/me", json={"discord_user_id": None})
    assert r.status_code == 200
    assert r.json()["discord_user_id"] is None


@pytest.mark.asyncio
async def test_update_rejects_non_digit_discord_id(user_client):
    r = await user_client.patch(
        "/api/v1/users/me", json={"discord_user_id": "not-a-number"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_rejects_too_short_discord_id(user_client):
    # 14 digits — Discord IDs are 15-20.
    r = await user_client.patch(
        "/api/v1/users/me", json={"discord_user_id": "12345678901234"}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_rejects_too_long_discord_id(user_client):
    # 21 digits — over range.
    r = await user_client.patch("/api/v1/users/me", json={"discord_user_id": "1" * 21})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_update_empty_string_coerced_to_null(user_client):
    """Frontend form submits '' when user clears the field — treat as null."""
    await user_client.patch(
        "/api/v1/users/me", json={"discord_user_id": "987654321098765432"}
    )
    r = await user_client.patch("/api/v1/users/me", json={"discord_user_id": ""})
    assert r.status_code == 200
    assert r.json()["discord_user_id"] is None
