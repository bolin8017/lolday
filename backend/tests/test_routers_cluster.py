"""Tests for app.routers.cluster — GPU status + Volcano queue depth endpoints."""

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_gpu_status_requires_auth(client):
    r = await client.get("/api/v1/cluster/gpu-status")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gpu_status_returns_allocation_dict(user_client):
    with patch(
        "app.routers.cluster.get_gpu_allocation",
        return_value={"total": 2, "in_use": 1, "idle": 1},
    ):
        r = await user_client.get("/api/v1/cluster/gpu-status")
    assert r.status_code == 200
    assert r.json() == {"total": 2, "in_use": 1, "idle": 1}


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
