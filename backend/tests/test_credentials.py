import pytest


@pytest.mark.asyncio
async def test_set_credential_stores_encrypted(auth_client_user):
    resp = await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "github"
    assert body["token_hint"] == "ghp_...6789"


@pytest.mark.asyncio
async def test_get_credential_returns_hint_not_token(auth_client_user):
    await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    resp = await auth_client_user.get("/api/v1/users/me/git-credential")
    assert resp.status_code == 200
    body = resp.json()
    assert "token" not in body
    assert body["token_hint"].endswith("6789")


@pytest.mark.asyncio
async def test_delete_credential(auth_client_user):
    await auth_client_user.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_abcdefghij0123456789"},
    )
    resp = await auth_client_user.delete("/api/v1/users/me/git-credential")
    assert resp.status_code == 204
    resp2 = await auth_client_user.get("/api/v1/users/me/git-credential")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client):
    resp = await client.get("/api/v1/users/me/git-credential")
    assert resp.status_code == 401
