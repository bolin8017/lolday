import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_build_token, require_job_token
from app.models import DatasetConfig, Job
from app.models.detector import DetectorBuild
from app.schemas.job import JobInternalConfig
from app.services.events_tail import event_broker, persist_event

router = APIRouter()


@router.post("/builds/{build_id}/schema")
async def submit_schema(
    payload: dict,
    build: DetectorBuild = Depends(require_build_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Called by validate init container with Pydantic JSON schema + git_sha."""
    if "schema" not in payload:
        raise HTTPException(status_code=422, detail="missing 'schema' in payload")
    build.pending_schema = payload["schema"]
    if payload.get("git_sha"):
        build.git_sha = payload["git_sha"]
    await session.commit()
    return {"accepted": True}


@router.get("/jobs/{job_id}/config", response_model=JobInternalConfig)
async def internal_get_job_config(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    job: Annotated[Job, Depends(require_job_token)],
) -> JobInternalConfig:
    train_csv = None
    test_csv = None
    predict_csv = None
    if job.train_dataset_id:
        ds = await session.get(DatasetConfig, job.train_dataset_id)
        train_csv = ds.csv_content if ds else None
    if job.test_dataset_id:
        ds = await session.get(DatasetConfig, job.test_dataset_id)
        test_csv = ds.csv_content if ds else None
    if job.predict_dataset_id:
        ds = await session.get(DatasetConfig, job.predict_dataset_id)
        predict_csv = ds.csv_content if ds else None
    yaml_text = job.resolved_config.get("yaml", "") if isinstance(job.resolved_config, dict) else ""
    return JobInternalConfig(
        yaml=yaml_text,
        train_csv=train_csv,
        test_csv=test_csv,
        predict_csv=predict_csv,
    )


@router.post("/jobs/{job_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    job_id: uuid.UUID,
    event: dict[str, Any],
    job: Job = Depends(require_job_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Receive a single event from the sidecar; persist + broadcast."""
    if job.id != job_id:
        raise HTTPException(status_code=404, detail="job_id mismatch")
    await persist_event(session, job_id=job.id, event=event)
    await event_broker.publish(job.id, event)
    return {"accepted": True}
