import uuid
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_session
from app.models import Job, Role, User
from app.models.detector import Detector
from app.models.job import NON_TERMINAL_STATUSES
from app.services.job_tokens import verify_token
from app.users import current_active_user

ROLE_HIERARCHY = {
    # Machine principal — strictly less privileged than any human role so
    # a service-token caller falling through to a require_role(...)-guarded
    # route gets a clean 403, not a 500 (KeyError). Phase 12.1.
    Role.SERVICE_TOKEN: -1,
    Role.USER: 0,
    Role.DEVELOPER: 1,
    Role.ADMIN: 2,
}


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


async def require_job_token(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Job:
    """Authenticate as a given job's init container via one-time token.

    Expected header: `Authorization: Bearer <token>`. Terminal jobs are
    rejected outright (H-20) even if a stale token_hash row exists.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    job = await session.get(Job, job_id)
    if job is None or job.token_hash is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=404, detail="job not found")
    if not verify_token(token, job.token_hash):
        raise HTTPException(status_code=403, detail="invalid token")
    return job
