from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import require_build_token
from app.models.detector import DetectorBuild

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
