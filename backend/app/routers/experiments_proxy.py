import asyncio
import logging
import mimetypes
from pathlib import PurePosixPath
from typing import Annotated, Any

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import User
from app.services.http_headers import build_content_disposition
from app.services.mlflow_client import MlflowClient, MlflowError
from app.users import current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)


_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)
# Grows unbounded (one Lock per experiment_id, never evicted). Acceptable: cache
# is capped at maxsize=64 and lab-scale experiment counts stay well under 1 k.
# Revisit if experiments become user-scoped or the cache cap is raised substantially.
_stats_locks: dict[str, asyncio.Lock] = {}


def _client() -> MlflowClient:
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


def _flatten_run(
    r: dict[str, Any],
    *,
    lolday_job_meta: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """MLflow REST returns runs as {info: {...}, data: {metrics: [{key,value}], ...}}.

    The frontend (and our aggregate logic) wants flat
    {run_id, status, metrics: {key: value}, params: {...}, tags: {...}}.
    Centralising the conversion here keeps the proxy contract simple and
    matches what callers already assume.

    ``lolday_job_meta`` (spec § 5.1 / § 6.8) maps the ``lolday.job_id`` tag
    value to its lolday-side ``Job.started_at`` / ``Job.finished_at`` ISO
    timestamps. The frontend Runs page renders Duration from those two so the
    column reflects compute time rather than wall-clock-from-submit (which is
    what MLflow's own ``info.start_time`` / ``info.end_time`` measure).
    """
    info = r.get("info") or {}
    data = r.get("data") or {}
    metrics_list = data.get("metrics") or []
    params_list = data.get("params") or []
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    out: dict[str, Any] = {
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
        "tags": tags,
        "lolday_started_at": None,
        "lolday_finished_at": None,
    }
    job_id = tags.get("lolday.job_id")
    if lolday_job_meta and job_id and job_id in lolday_job_meta:
        out["lolday_started_at"] = lolday_job_meta[job_id]["started_at"]
        out["lolday_finished_at"] = lolday_job_meta[job_id]["finished_at"]
    return out


async def _fetch_lolday_job_meta(
    run_ids: list[str],
    session: AsyncSession,
) -> dict[str, dict[str, str | None]]:
    """Map lolday Job.id (string) → {started_at, finished_at} ISO strings.

    Lookup is by ``Job.mlflow_run_id`` IN (...) so it can batch any number of
    runs in one query. The frontend joins on the ``lolday.job_id`` tag, so the
    return-dict is keyed by ``Job.id`` as a string.
    """
    from app.models.job import Job

    if not run_ids:
        return {}
    stmt = select(Job).where(Job.mlflow_run_id.in_(run_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {
        str(j.id): {
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        }
        for j in rows
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
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        raw = await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    run_ids: list[str] = []
    for r in raw:
        info = r.get("info") or {}
        rid = info.get("run_id") or info.get("run_uuid")
        if isinstance(rid, str) and rid:
            run_ids.append(rid)
    lolday_meta = await _fetch_lolday_job_meta(run_ids, session)
    return [_flatten_run(r, lolday_job_meta=lolday_meta) for r in raw]


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        raw = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    lolday_meta = await _fetch_lolday_job_meta([run_id], session)
    return _flatten_run(raw, lolday_job_meta=lolday_meta)


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
        headers={"Content-Disposition": build_content_disposition(filename)},
    )
