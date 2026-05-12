import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_oversized_body_rejected_with_413(user_client: AsyncClient, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "BODY_SIZE_MAX_BYTES", 1024)
    payload = "x" * 4096
    r = await user_client.post(
        "/api/v1/datasets",
        headers={"Content-Length": str(len(payload) + 100)},
        content=payload.encode(),
    )
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
async def test_undersized_body_passes_middleware(user_client: AsyncClient):
    # A normal small request should not hit the middleware.
    r = await user_client.get("/api/v1/health")
    assert r.status_code == 200
