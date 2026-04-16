import secrets
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models import Role, User
from app.models.detector import Detector, DetectorBuild, DetectorBuildStatus
from app.users import current_active_user

ROLE_HIERARCHY = {Role.USER: 0, Role.DEVELOPER: 1, Role.ADMIN: 2}


def require_role(min_role: Role):
    async def _check(user: User = Depends(current_active_user)):
        if ROLE_HIERARCHY[user.role] < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check


async def load_detector(
    detector_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> Detector:
    d = await session.get(Detector, detector_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status_code=404, detail="detector not found")
    return d


def require_detector_access(write: bool = False):
    """Build a dep that ensures caller is owner or admin.

    write=False: any authenticated user can read
    write=True: owner or admin only
    """
    async def _inner(
        detector: Detector = Depends(load_detector),
        user: User = Depends(current_active_user),
    ) -> Detector:
        if not write:
            return detector
        if user.role == Role.ADMIN or detector.owner_id == user.id:
            return detector
        raise HTTPException(status_code=403, detail="not owner / admin")

    return _inner


def generate_build_token() -> str:
    return f"btok_{secrets.token_urlsafe(32)}"


async def require_build_token(
    build_id: UUID,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorBuild:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    build = await session.get(DetectorBuild, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    if build.build_token != token:
        raise HTTPException(status_code=401, detail="invalid build token")
    # Allow submission during VALIDATING/BUILDING/CLONING/PENDING (any in-flight pre-scan state)
    if build.status not in {
        DetectorBuildStatus.PENDING,
        DetectorBuildStatus.CLONING,
        DetectorBuildStatus.VALIDATING,
        DetectorBuildStatus.BUILDING,
    }:
        raise HTTPException(status_code=400, detail="build not in schema-accepting state")
    return build
