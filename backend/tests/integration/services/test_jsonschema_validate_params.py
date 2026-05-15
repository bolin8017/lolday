"""jsonschema-based user params validation — replaces phase 11c hand-rolled guard."""

from __future__ import annotations

import pytest
from app.services.jobs_params_validate import (
    UserParamsRejected,
    validate_user_params,
)

SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "n_estimators": {"type": "integer", "minimum": 1},
        "lr": {"type": "number", "exclusiveMinimum": 0.0},
        "nested": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"dim": {"type": "integer", "minimum": 1}},
        },
    },
    "required": [],
}


def test_valid_params_pass() -> None:
    validate_user_params(params={"n_estimators": 100, "lr": 0.01}, schema=SCHEMA)


def test_extra_field_rejected_with_path() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"unknown": 1}, schema=SCHEMA)
    assert "unknown" in str(ei.value)


def test_type_mismatch_rejected_with_pointer() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"n_estimators": "many"}, schema=SCHEMA)
    assert "/n_estimators" in str(ei.value)


def test_out_of_range_rejected_with_pointer() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"n_estimators": 0}, schema=SCHEMA)
    assert "/n_estimators" in str(ei.value)


def test_exclusive_minimum_rejected() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"lr": 0.0}, schema=SCHEMA)
    assert "/lr" in str(ei.value)


def test_nested_extra_rejected() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(params={"nested": {"unknown": 1}}, schema=SCHEMA)
    assert "/nested" in str(ei.value)


def test_empty_params_pass() -> None:
    validate_user_params(params={}, schema=SCHEMA)


def test_aggregates_multiple_errors() -> None:
    with pytest.raises(UserParamsRejected) as ei:
        validate_user_params(
            params={"n_estimators": 0, "lr": -1.0},
            schema=SCHEMA,
        )
    msg = str(ei.value)
    assert "/n_estimators" in msg
    assert "/lr" in msg
