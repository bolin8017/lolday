import httpx
import pytest
import respx

from app.routers import detectors as dr
from tests.conftest import test_session_maker


@pytest.mark.asyncio
async def test_available_tags_calls_github(auth_client_developer, monkeypatch):
    # First register a detector (reuse Task 8 approach)
    async def fake_meta(url, pat):
        return {"name": "upxelfdet", "description": "demo", "display_name": "upxelfdet"}
    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)

    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    detector_id = create.json()["id"]

    with respx.mock(base_url="https://api.github.com") as mock:
        mock.get("/repos/bolin8017/upxelfdet/tags").mock(
            return_value=httpx.Response(200, json=[
                {"name": "v0.1.0", "commit": {"sha": "abcdef1234"}},
                {"name": "v0.0.1", "commit": {"sha": "fedcba4321"}},
            ])
        )
        resp = await auth_client_developer.get(
            f"/api/v1/detectors/{detector_id}/available-tags"
        )
        assert resp.status_code == 200
        tags = resp.json()
        assert len(tags) == 2
        assert tags[0]["name"] == "v0.1.0"


@pytest.mark.asyncio
async def test_versions_empty_initially(auth_client_developer, monkeypatch):
    async def fake_meta(url, pat):
        return {"name": "upxelfdet", "description": "demo", "display_name": "upxelfdet"}
    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)

    create = await auth_client_developer.post(
        "/api/v1/detectors", json={"git_url": "https://github.com/bolin8017/upxelfdet"}
    )
    detector_id = create.json()["id"]

    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{detector_id}/versions"
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
