"""Flat builds API — alias for ``GET /api/v1/detectors/{id}/builds/{build_id}``.

Polling scripts naturally try ``/builds/<id>`` first before discovering
the nested path; this router forwards while applying the same visibility
rule as the nested route: any authenticated user can read any build
(build data is considered internally visible within the platform, same
as detector metadata).

If the build does not exist, returns 404. No existence-leak avoidance
beyond what the nested route already provides — keep the two paths in
lockstep so behaviour doesn't drift.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models.detector import Detector, DetectorBuild
from app.models.user import User
from app.schemas.detector import BuildRead
from app.users import current_active_user

router = APIRouter()


@router.get("/{build_id}", response_model=BuildRead)
async def get_build_flat(
    build_id: UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BuildRead:
    build = await session.get(DetectorBuild, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    # Fetch the parent detector so the schema validator can render
    # harbor_image etc. — and to give us a single place to extend with
    # per-detector ACLs if the nested route ever tightens read access.
    detector = await session.get(Detector, build.detector_id)
    if detector is None:
        raise HTTPException(status_code=404, detail="build not found")
    return BuildRead.model_validate(build)
