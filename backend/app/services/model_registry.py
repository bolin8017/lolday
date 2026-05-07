"""Model Registry transition rules and MLflow sync.

Transition matrix (intended rules):

  source \\ target  | None     | Staging  | Production | Archived
  ------------------|----------|----------|------------|----------
  None              | noop     | D/O or A | D/O or A   | D/O or A
  Staging           | admin    | noop     | D/O or A   | D/O or A
  Production        | admin    | admin    | noop       | D/O or A
  Archived          | admin    | admin    | admin      | noop

Legend: D/O = developer (must be owner); A = admin; admin = admin only.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.detector import Detector
from app.models.model_registry import (
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    RegisteredModel,
)
from app.models.user import User


class InvalidTransitionError(ValueError):
    pass


_ADMIN_ONLY_TARGETS_FROM_ARCHIVED = {
    ModelVersionStage.NONE,
    ModelVersionStage.STAGING,
    ModelVersionStage.PRODUCTION,
}

_ADMIN_ONLY_TARGETS_FROM_PRODUCTION = {
    ModelVersionStage.NONE,
    ModelVersionStage.STAGING,
}

_ADMIN_ONLY_TARGETS_FROM_STAGING = {
    ModelVersionStage.NONE,
}


def validate_transition(
    from_stage: ModelVersionStage,
    to_stage: ModelVersionStage,
    *,
    actor_role: str,
    is_owner: bool,
) -> None:
    """Raise InvalidTransitionError if not allowed.

    `actor_role` is one of 'admin', 'developer', 'user'.
    `is_owner` is True iff actor owns the source job that produced this model.
    """
    if from_stage == to_stage:
        return

    if actor_role == "admin":
        return

    if actor_role != "developer":
        raise InvalidTransitionError(
            f"role {actor_role!r}: only developer or admin can transition model stages"
        )

    if not is_owner:
        raise InvalidTransitionError(
            "non-owner developer cannot transition; must be model owner or admin"
        )

    admin_only = set()
    if from_stage == ModelVersionStage.ARCHIVED:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_ARCHIVED
    elif from_stage == ModelVersionStage.PRODUCTION:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_PRODUCTION
    elif from_stage == ModelVersionStage.STAGING:
        admin_only = _ADMIN_ONLY_TARGETS_FROM_STAGING

    if to_stage in admin_only:
        raise InvalidTransitionError(
            f"transition {from_stage.value} → {to_stage.value} requires admin"
        )


async def resolve_registered_model(
    owner: str,
    name: str,
    session: AsyncSession,
    user: User,
    *,
    write: bool = False,
) -> RegisteredModel:
    """Centralised access control for `/models/{owner}/{name}/...` endpoints.

    Read path: returns 404 if the model doesn't exist OR if every version is
    private and the caller isn't owner/admin (hide-existence pattern, mirrors
    ``datasets._get_readable_dataset``).

    Write path: returns 403 if caller isn't owner/admin (mirrors
    ``datasets._get_writable_dataset``).
    """
    rm = (
        await session.execute(
            select(RegisteredModel)
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .where(User.handle == owner, Detector.name == name)
        )
    ).scalar_one_or_none()
    if rm is None:
        raise HTTPException(404, "model not found")

    is_owner = rm.owner_id == user.id
    is_admin = user.role.value == "admin"

    if write and not (is_owner or is_admin):
        raise HTTPException(403, "owner or admin only")

    if not write and not (is_owner or is_admin):
        # Read path: must have at least one publicly-visible version
        any_visible = (
            await session.execute(
                select(func.count())
                .select_from(ModelVersion)
                .where(
                    ModelVersion.registered_model_id == rm.id,
                    ModelVersion.visibility == ModelVersionVisibility.PUBLIC,
                )
            )
        ).scalar()
        if not any_visible:
            raise HTTPException(404, "model not found")  # hide-existence

    return rm
