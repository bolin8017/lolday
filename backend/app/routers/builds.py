"""Flat builds API — alias for the nested /detectors/{id}/builds/{build_id}.

Phase 8 finding: polling scripts naturally reach for
``GET /api/v1/builds/<id>`` before discovering builds are nested under
their detector. This router satisfies that expectation by looking up the
build's detector internally and enforcing the same access check the
nested route applies.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models.detector import Detector, DetectorBuild
from app.schemas.detector import BuildRead
from app.users import current_active_user
from app.models.user import User


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
    detector = await session.get(Detector, build.detector_id)
    if detector is None:
        raise HTTPException(status_code=404, detail="build not found")
    # Same visibility rules as the nested route: owner or admin.
    if detector.owner_id != user.id and user.role.value != "admin":
        raise HTTPException(status_code=404, detail="build not found")
    return BuildRead.model_validate(build)
