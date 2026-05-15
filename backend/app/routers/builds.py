"""Flat builds API -- alias for ``GET /api/v1/detectors/{id}/builds/{build_id}``.

Polling scripts naturally try ``/builds/<id>`` first before discovering
the nested path; this router forwards while applying the same visibility
rule as the nested route.

#163 (security-hardening 2026-05-15): owner-or-admin only. The previous
behaviour returned any build to any authenticated user (cross-tenant
read). Match `require_detector_access(write=False)` semantics: 404 (not
403) on non-owner so we don't leak build existence.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.deps import load_detector
from app.models.detector import DetectorBuild
from app.models.user import Role, User
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
    # Re-use load_detector for the soft-delete check (same semantics as the
    # nested route). load_detector raises HTTPException(404) on missing /
    # soft-deleted; let it propagate.
    detector = await load_detector(detector_id=build.detector_id, session=session)
    # #163: enforce owner-or-admin. 404 (not 403) -- avoid leaking build
    # existence to non-owners. Mirrors the require_detector_access(write=False)
    # check pattern.
    if detector.owner_id != user.id and user.role != Role.ADMIN:
        raise HTTPException(status_code=404, detail="build not found")
    return BuildRead.model_validate(build)
