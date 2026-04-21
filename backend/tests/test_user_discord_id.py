"""Tests for User.discord_user_id column + UserUpdate schema exposure."""

import pytest


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
