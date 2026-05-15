import pytest
from app.models.model_registry import ModelVersionStage
from app.services.model_registry import (
    InvalidTransitionError,
    validate_transition,
)


@pytest.mark.parametrize(
    "from_stage,to_stage",
    [
        (ModelVersionStage.NONE, ModelVersionStage.STAGING),
        (ModelVersionStage.STAGING, ModelVersionStage.PRODUCTION),
        (ModelVersionStage.PRODUCTION, ModelVersionStage.ARCHIVED),
        (ModelVersionStage.STAGING, ModelVersionStage.ARCHIVED),
        (ModelVersionStage.NONE, ModelVersionStage.PRODUCTION),
    ],
)
def test_valid_forward_transitions(from_stage, to_stage):
    validate_transition(from_stage, to_stage, actor_role="developer", is_owner=True)


def test_archived_to_none_admin_only():
    with pytest.raises(InvalidTransitionError, match="admin"):
        validate_transition(
            ModelVersionStage.ARCHIVED,
            ModelVersionStage.NONE,
            actor_role="developer",
            is_owner=True,
        )
    validate_transition(
        ModelVersionStage.ARCHIVED,
        ModelVersionStage.NONE,
        actor_role="admin",
        is_owner=False,
    )


def test_archived_to_staging_admin_only():
    with pytest.raises(InvalidTransitionError, match="admin"):
        validate_transition(
            ModelVersionStage.ARCHIVED,
            ModelVersionStage.STAGING,
            actor_role="developer",
            is_owner=True,
        )


def test_user_role_denied_for_transitions():
    with pytest.raises(InvalidTransitionError, match="developer"):
        validate_transition(
            ModelVersionStage.STAGING,
            ModelVersionStage.PRODUCTION,
            actor_role="user",
            is_owner=True,
        )


def test_developer_must_be_owner():
    with pytest.raises(InvalidTransitionError, match="owner"):
        validate_transition(
            ModelVersionStage.NONE,
            ModelVersionStage.STAGING,
            actor_role="developer",
            is_owner=False,
        )


def test_admin_can_transition_anyone():
    validate_transition(
        ModelVersionStage.NONE,
        ModelVersionStage.PRODUCTION,
        actor_role="admin",
        is_owner=False,
    )


def test_same_stage_is_noop_no_error():
    validate_transition(
        ModelVersionStage.PRODUCTION,
        ModelVersionStage.PRODUCTION,
        actor_role="admin",
        is_owner=False,
    )
