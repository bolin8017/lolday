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

from app.models.model_registry import ModelVersionStage


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
