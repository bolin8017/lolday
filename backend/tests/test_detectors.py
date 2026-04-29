import pytest


@pytest.mark.asyncio
async def test_register_rejects_user_role(auth_client_user, monkeypatch):
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    resp = await auth_client_user.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/upxelfdet"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_register_developer(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    resp = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/upxelfdet"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "upxelfdet"


@pytest.mark.asyncio
async def test_register_duplicate_git_url(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    r1 = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    r2 = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    assert r1.status_code == 201
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_and_get(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    did = create.json()["id"]
    lst = await auth_client_developer.get("/api/v1/detectors")
    assert lst.status_code == 200
    assert any(d["id"] == did for d in lst.json()["items"])
    one = await auth_client_developer.get(f"/api/v1/detectors/{did}")
    assert one.status_code == 200


@pytest.mark.asyncio
async def test_soft_delete(auth_client_developer, monkeypatch):
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_clone_and_validate", _fake_meta("upxelfdet"))

    # Also monkeypatch the Harbor cleanup to avoid real API call
    async def _noop_cleanup(*a, **kw):
        pass

    monkeypatch.setattr(dr, "_delete_harbor_images", _noop_cleanup)
    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    did = create.json()["id"]
    d = await auth_client_developer.delete(f"/api/v1/detectors/{did}")
    assert d.status_code == 204
    g = await auth_client_developer.get(f"/api/v1/detectors/{did}")
    assert g.status_code == 404


@pytest.mark.asyncio
async def test_register_git_clone_uses_safe_env(auth_client_developer, monkeypatch):
    """Git clone subprocess must set GIT_TERMINAL_PROMPT=0 to avoid password prompts."""
    from app.routers import detectors as dr

    captured = {}

    async def fake_accessible(*a, **kw):
        return True

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})

        class FakeProc:
            returncode = 1

            async def communicate(self):
                return (b"", b"fake error\n")

            def kill(self):
                pass

            async def wait(self):
                pass

        return FakeProc()

    monkeypatch.setattr(dr, "check_repo_accessible", fake_accessible)
    monkeypatch.setattr(dr.asyncio, "create_subprocess_exec", fake_exec)

    resp = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/upxelfdet"},
    )
    # Expect 400 (clone failure) — but what matters is env was set
    assert resp.status_code == 400
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GIT_ASKPASS"] == ""


def _fake_meta(name: str):
    async def _inner(url, pat):
        return {"name": name, "description": "demo", "display_name": name}

    return _inner
