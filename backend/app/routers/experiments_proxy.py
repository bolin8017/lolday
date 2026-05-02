import asyncio
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.models import User
from app.services.mlflow_client import MlflowClient, MlflowError
from app.users import current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _build_content_disposition(filename: str) -> str:
    """RFC 6266 dual-form ``Content-Disposition`` for artifact downloads.

    Output: ``attachment; filename="<ascii>"; filename*=UTF-8''<percent-encoded>``.

    - ``filename``: ASCII fallback for legacy clients. Non-ASCII chars become ``?``
      via ``encode("ascii", errors="replace")`` and quotes are scrubbed to ``_`` to
      defend against header-injection.
    - ``filename*``: RFC 5987 percent-encoded UTF-8 form, used by every modern
      browser. Lab-produced detectors may emit Chinese-named files, so the dual
      form is required (the ASCII fallback alone would lose the filename).
    """
    ascii_fallback = (
        filename.encode("ascii", errors="replace").decode("ascii").replace('"', "_")
    )
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)
# Grows unbounded (one Lock per experiment_id, never evicted). Acceptable: cache
# is capped at maxsize=64 and lab-scale experiment counts stay well under 1 k.
# Revisit if experiments become user-scoped or the cache cap is raised substantially.
_stats_locks: dict[str, asyncio.Lock] = {}


def _client() -> MlflowClient:
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


def _flatten_run(r: dict[str, Any]) -> dict[str, Any]:
    """MLflow REST returns runs as {info: {...}, data: {metrics: [{key,value}], ...}}.

    The frontend (and our aggregate logic) wants flat
    {run_id, status, metrics: {key: value}, params: {...}, tags: {...}}.
    Centralising the conversion here keeps the proxy contract simple and
    matches what callers already assume.
    """
    info = r.get("info") or {}
    data = r.get("data") or {}
    metrics_list = data.get("metrics") or []
    params_list = data.get("params") or []
    tags_list = data.get("tags") or []
    return {
        "run_id": info.get("run_id") or info.get("run_uuid"),
        "run_name": info.get("run_name"),
        "experiment_id": info.get("experiment_id"),
        "status": info.get("status"),
        "start_time": info.get("start_time"),
        "end_time": info.get("end_time"),
        "artifact_uri": info.get("artifact_uri"),
        "lifecycle_stage": info.get("lifecycle_stage"),
        "metrics": {m["key"]: m["value"] for m in metrics_list if "key" in m},
        "params": {p["key"]: p["value"] for p in params_list if "key" in p},
        "tags": {t["key"]: t["value"] for t in tags_list if "key" in t},
    }


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
        raw = await _client().search_runs([experiment_id], max_results=1000)
        runs = [_flatten_run(r) for r in raw]
        f1s = [r["metrics"].get("f1") for r in runs if r.get("status") == "FINISHED"]
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
        raw = await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return [_flatten_run(r) for r in raw]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        raw = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _flatten_run(raw)


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

    # RFC 6266: tell the browser to save with the artifact basename instead of
    # the URL's literal "download" segment. See spec §5.2 / plan Task 2.3.
    filename = PurePosixPath(path).name or "artifact"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=r.content,
        media_type=media_type,
        headers={"Content-Disposition": _build_content_disposition(filename)},
    )
