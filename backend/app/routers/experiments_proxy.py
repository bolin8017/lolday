import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import settings
from app.models import User
from app.services.mlflow_client import MlflowClient, MlflowError
from app.users import current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _client() -> MlflowClient:
    return MlflowClient(
        settings.MLFLOW_TRACKING_URI, timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
    )


@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        return await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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
