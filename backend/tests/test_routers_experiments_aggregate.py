"""Phase 13b B4: experiments aggregate endpoint with manual async TTL cache."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_experiments_no_include_returns_bare_list(
    async_client, auth_owner_headers
):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    with patch("app.routers.experiments_proxy._client") as mc:
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        resp = await async_client.get("/api/v1/experiments", headers=auth_owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == fake_experiments
    assert "run_count" not in body[0]


@pytest.mark.asyncio
async def test_experiments_with_stats_aggregates(async_client, auth_owner_headers):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    fake_runs = [
        {
            "run_id": "r1",
            "status": "FINISHED",
            "start_time": 1700000000000,
            "metrics": {"f1": 0.91},
        },
        {
            "run_id": "r2",
            "status": "FINISHED",
            "start_time": 1700001000000,
            "metrics": {"f1": 0.93},
        },
        {
            "run_id": "r3",
            "status": "RUNNING",
            "start_time": 1700002000000,
            "metrics": {},
        },
    ]
    with (
        patch("app.routers.experiments_proxy._client") as mc,
        patch(
            "app.routers.experiments_proxy._stats_cache",
            new_callable=lambda: __import__("cachetools").TTLCache(maxsize=64, ttl=30),
        ),
    ):
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        mc.return_value.search_runs = AsyncMock(return_value=fake_runs)
        resp = await async_client.get(
            "/api/v1/experiments?include=stats",
            headers=auth_owner_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["run_count"] == 3
    assert body[0]["best_f1"] == pytest.approx(0.93)
    assert body[0]["latest_start_time"] == 1700002000000


@pytest.mark.asyncio
async def test_experiments_stats_cached(async_client, auth_owner_headers):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    runs_called = 0

    async def mock_search_runs(experiment_ids, max_results):
        nonlocal runs_called
        runs_called += 1
        return [
            {
                "run_id": "r1",
                "status": "FINISHED",
                "start_time": 1,
                "metrics": {"f1": 0.5},
            }
        ]

    with (
        patch("app.routers.experiments_proxy._client") as mc,
        patch(
            "app.routers.experiments_proxy._stats_cache",
            new_callable=lambda: __import__("cachetools").TTLCache(maxsize=64, ttl=30),
        ),
    ):
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        mc.return_value.search_runs = AsyncMock(side_effect=mock_search_runs)

        await async_client.get(
            "/api/v1/experiments?include=stats", headers=auth_owner_headers
        )
        await async_client.get(
            "/api/v1/experiments?include=stats", headers=auth_owner_headers
        )
    assert runs_called == 1  # second call hit cache
