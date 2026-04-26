"""Tests for the Hydra-meta + platform-prefix params guard (Phase 11c)."""

from __future__ import annotations

import pytest

from app.services.jobs_params_guard import (
    UserParamsRejected,
    validate_user_params,
)


def test_simple_overrides_pass() -> None:
    validate_user_params({"model.n_estimators": 500, "trainer.max_epochs": 3})


def test_nested_dict_pass() -> None:
    validate_user_params({"model": {"n_estimators": 500, "max_depth": 8}})


@pytest.mark.parametrize("key", ["_target_", "_partial_", "_args_", "_recursive_"])
def test_hydra_meta_rejected_at_top_level(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({key: "evil.module.func"})


@pytest.mark.parametrize("key", ["_target_", "_partial_", "_args_", "_recursive_"])
def test_hydra_meta_rejected_when_nested(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({"model": {key: "evil.module.func"}})


def test_dotted_hydra_meta_rejected() -> None:
    with pytest.raises(UserParamsRejected, match="_target_"):
        validate_user_params({"model._target_": "evil.module.func"})


@pytest.mark.parametrize("prefix", ["paths", "data", "mlflow"])
def test_platform_controlled_prefix_rejected(prefix: str) -> None:
    with pytest.raises(UserParamsRejected, match=prefix):
        validate_user_params({f"{prefix}.output_dir": "/anywhere"})


@pytest.mark.parametrize("prefix", ["paths", "data", "mlflow"])
def test_platform_controlled_prefix_dict_rejected(prefix: str) -> None:
    with pytest.raises(UserParamsRejected, match=prefix):
        validate_user_params({prefix: {"output_dir": "/anywhere"}})


def test_empty_params_pass() -> None:
    validate_user_params({})


def test_non_dict_rejected() -> None:
    with pytest.raises(UserParamsRejected, match="must be a dict"):
        validate_user_params(["not", "a", "dict"])  # type: ignore[arg-type]
