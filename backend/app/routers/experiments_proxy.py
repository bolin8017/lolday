import asyncio
import logging
from typing import Annotated

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.models import User
from app.services.mlflow_client import MlflowClient, MlflowError
from app.users import current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)

_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)
_stats_locks: dict[str, asyncio.Lock] = {}


def _client() -> MlflowClient:
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
    include: str | None = Query(None, pattern="^stats$"),
):
    try:
        experiments = await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if include != "stats":
        return experiments

    enriched = []
    for exp in experiments:
        try:
            stats = await _experiment_stats(exp["experiment_id"])
        except MlflowError as e:
            # Stats failure shouldn't poison the whole list; degrade gracefully.
            logger.warning(
                "experiment_stats failed for %s: %s", exp["experiment_id"], e
            )
            stats = {"run_count": None, "best_f1": None, "latest_start_time": None}
        enriched.append({**exp, **stats})
    return enriched


async def _experiment_stats(experiment_id: str) -> dict:
    """Async TTL-cached aggregate. cachetools.@cached doesn't support async,
    so we cache by hand with a per-key Lock to avoid stampede."""
    if experiment_id in _stats_cache:
        return _stats_cache[experiment_id]
    lock = _stats_locks.setdefault(experiment_id, asyncio.Lock())
    async with lock:
        if experiment_id in _stats_cache:  # double-check after acquiring lock
            return _stats_cache[experiment_id]
        runs = await _client().search_runs([experiment_id], max_results=1000)
        f1s = [
            r.get("metrics", {}).get("f1")
            for r in runs
            if r.get("status") == "FINISHED"
        ]
        f1s = [x for x in f1s if x is not None]
        result = {
            "run_count": len(runs),
            "best_f1": max(f1s) if f1s else None,
            "latest_start_time": max(
                (r["start_time"] for r in runs if r.get("start_time")), default=None
            ),
        }
        _stats_cache[experiment_id] = result
        return result


@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        return await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        return await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
    path: str | None = None,
):
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/artifacts/list"
    params = {"run_id": run_id}
    if path:
        params["path"] = path
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return r.json()


@router.get("/runs/{run_id}/artifacts/download")
async def download_artifact(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    run = await _client().get_run(run_id)
    artifact_uri: str = run["info"]["artifact_uri"]
    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise HTTPException(
            status_code=502,
            detail=f"unexpected artifact_uri scheme: {artifact_uri!r}",
        )
    relative = artifact_uri[len(prefix) :].rstrip("/")
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/{relative}/{path}"
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return Response(content=r.content, media_type="application/octet-stream")
