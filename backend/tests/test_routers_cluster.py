"""Tests for app.routers.cluster — GPU status + Volcano queue depth endpoints."""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_gpu_status_requires_auth(client):
    r = await client.get("/api/v1/cluster/gpu-status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gpu_status_returns_new_allocation_schema(user_client):
    new_schema = {
        "total": 2,
        "free_count": 1,
        "in_use_by_lolday": 1,
        "in_use_by_external": 0,
        "fail_safe_active": False,
        "fail_safe_reason": None,
        "per_gpu": [
            {
                "gpu_id": 0,
                "state": "lolday",
                "util_percent": 87.5,
                "vram_used_mb": 9240,
            },
            {"gpu_id": 1, "state": "free", "util_percent": 0.0, "vram_used_mb": 0},
        ],
    }
    with patch(
        "app.routers.cluster.get_gpu_allocation",
        return_value=new_schema,
    ):
        r = await user_client.get("/api/v1/cluster/gpu-status")
    assert r.status_code == 200
    assert r.json() == new_schema


@pytest.mark.asyncio
async def test_queue_requires_auth(client):
    r = await client.get("/api/v1/cluster/queue")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_queue_returns_depth(user_client):
    with patch("app.routers.cluster.get_queue_depth", return_value=3):
        r = await user_client.get("/api/v1/cluster/queue")
    assert r.status_code == 200
    assert r.json() == {"depth": 3}
