"""Phase 13b B4: experiments aggregate endpoint with manual async TTL cache.

Run shapes follow the MLflow REST contract: each run is
``{"info": {...}, "data": {"metrics": [{"key", "value"}], ...}}``.
The proxy flattens this for callers; tests exercise the flatten + aggregate
path together.
"""

from unittest.mock import AsyncMock, patch

import pytest
from app.routers.experiments_proxy import _flatten_run


def _run(
    run_id: str,
    status: str,
    start_time: int,
    metrics: dict[str, float] | None = None,
    params: dict[str, str] | None = None,
    tags: dict[str, str] | None = None,
) -> dict:
    """Build an MLflow-shaped run for tests."""
    return {
        "info": {
            "run_id": run_id,
            "experiment_id": "1",
            "status": status,
            "start_time": start_time,
            "end_time": start_time + 1000,
            "run_name": run_id,
        },
        "data": {
            "metrics": [{"key": k, "value": v} for k, v in (metrics or {}).items()],
            "params": [{"key": k, "value": v} for k, v in (params or {}).items()],
            "tags": [{"key": k, "value": v} for k, v in (tags or {}).items()],
        },
    }


def test_flatten_run_nested_to_flat() -> None:
    raw = _run(
        "r1",
        "FINISHED",
        100,
        metrics={"f1": 0.9, "accuracy": 0.95},
        params={"lr": "0.01"},
        tags={"lolday.job_id": "job-x"},
    )
    flat = _flatten_run(raw)
    assert flat["run_id"] == "r1"
    assert flat["status"] == "FINISHED"
    assert flat["start_time"] == 100
    assert flat["metrics"] == {"f1": 0.9, "accuracy": 0.95}
    assert flat["params"] == {"lr": "0.01"}
    assert flat["tags"] == {"lolday.job_id": "job-x"}


def test_flatten_run_handles_missing_data_and_info() -> None:
    # Defensive: MLflow has been observed to omit data sub-keys on minimal runs.
    flat = _flatten_run({"info": {"run_uuid": "r2"}})
    assert flat["run_id"] == "r2"
    assert flat["metrics"] == {}
    assert flat["params"] == {}
    assert flat["tags"] == {}


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
        _run("r1", "FINISHED", 1700000000000, metrics={"f1": 0.91}),
        _run("r2", "FINISHED", 1700001000000, metrics={"f1": 0.93}),
        _run("r3", "RUNNING", 1700002000000),
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
        return [_run("r1", "FINISHED", 1, metrics={"f1": 0.5})]

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


@pytest.mark.asyncio
async def test_list_runs_returns_flat_shape(async_client, auth_owner_headers):
    fake_runs = [
        _run(
            "r1",
            "FINISHED",
            123,
            metrics={"f1": 0.9},
            tags={"lolday.job_id": "job-1"},
        )
    ]
    with patch("app.routers.experiments_proxy._client") as mc:
        mc.return_value.search_runs = AsyncMock(return_value=fake_runs)
        resp = await async_client.get(
            "/api/v1/experiments/1/runs", headers=auth_owner_headers
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["run_id"] == "r1"
    assert body[0]["status"] == "FINISHED"
    assert body[0]["metrics"] == {"f1": 0.9}
    assert body[0]["tags"]["lolday.job_id"] == "job-1"
    assert "info" not in body[0]


@pytest.mark.asyncio
async def test_get_run_returns_flat_shape(async_client, auth_owner_headers):
    raw = _run("r1", "FINISHED", 123, params={"lr": "0.01"})
    with patch("app.routers.experiments_proxy._client") as mc:
        mc.return_value.get_run = AsyncMock(return_value=raw)
        resp = await async_client.get("/api/v1/runs/r1", headers=auth_owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "r1"
    assert body["params"] == {"lr": "0.01"}
    assert "info" not in body


def test_flatten_run_attaches_lolday_timestamps_when_meta_provided() -> None:
    """Spec § 5.1 / § 6.8 — flat dict surfaces lolday Job timestamps for the FE Duration column."""
    raw = _run("r9", "FINISHED", 0, tags={"lolday.job_id": "job-9"})
    meta = {
        "job-9": {
            "started_at": "2026-05-11T10:05:00+00:00",
            "finished_at": "2026-05-11T10:15:00+00:00",
        }
    }
    flat = _flatten_run(raw, lolday_job_meta=meta)
    assert flat["lolday_started_at"] == "2026-05-11T10:05:00+00:00"
    assert flat["lolday_finished_at"] == "2026-05-11T10:15:00+00:00"


def test_flatten_run_lolday_timestamps_none_when_no_meta() -> None:
    raw = _run("r10", "FINISHED", 0, tags={"lolday.job_id": "job-orphan"})
    flat = _flatten_run(raw, lolday_job_meta={})
    assert flat["lolday_started_at"] is None
    assert flat["lolday_finished_at"] is None


def test_flatten_run_lolday_timestamps_none_when_tag_missing() -> None:
    raw = _run("r11", "FINISHED", 0, tags={})  # no lolday.job_id tag
    meta = {"job-9": {"started_at": "2026-05-11T10:00:00+00:00", "finished_at": None}}
    flat = _flatten_run(raw, lolday_job_meta=meta)
    assert flat["lolday_started_at"] is None
    assert flat["lolday_finished_at"] is None
