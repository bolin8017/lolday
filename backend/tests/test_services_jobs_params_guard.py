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


@pytest.mark.parametrize(
    "key", ["_target_", "_partial_", "_args_", "_recursive_", "_convert_"]
)
def test_hydra_meta_rejected_at_top_level(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({key: "evil.module.func"})


@pytest.mark.parametrize(
    "key", ["_target_", "_partial_", "_args_", "_recursive_", "_convert_"]
)
def test_hydra_meta_rejected_when_nested(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({"model": {key: "evil.module.func"}})


@pytest.mark.parametrize(
    "key", ["_target_", "_partial_", "_args_", "_recursive_", "_convert_"]
)
def test_dotted_hydra_meta_rejected(key: str) -> None:
    with pytest.raises(UserParamsRejected, match=key):
        validate_user_params({f"model.{key}": "evil.module.func"})


@pytest.mark.parametrize(
    "nested_key",
    [
        "model._target_",
        "trainer.callbacks._target_",
        "deep.path._partial_",
    ],
)
def test_nested_dotted_hydra_meta_rejected(nested_key: str) -> None:
    """Nested dotted keys containing Hydra meta-fields must be rejected.

    Regression test for the original guard bug — _walk only checked
    `key in HYDRA_META_KEYS` (literal membership) without splitting
    on `.`, letting `{"foo": {"model._target_": "evil"}}` slip through.
    """
    with pytest.raises(UserParamsRejected, match=nested_key.split(".")[-1]):
        validate_user_params({"foo": {nested_key: "evil.module.func"}})


def test_three_level_nested_hydra_meta_rejected() -> None:
    """Hydra meta in any segment, at any depth, must be rejected."""
    with pytest.raises(UserParamsRejected):
        validate_user_params({"a": {"b": {"deep._target_": "evil"}}})


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


def test_list_of_dicts_with_hydra_meta_rejected() -> None:
    """Hydra resolves _target_ inside list elements (e.g. callback lists)."""
    with pytest.raises(UserParamsRejected, match="_target_"):
        validate_user_params({"callbacks": [{"_target_": "evil.module"}]})


def test_deeply_nested_list_with_hydra_meta_rejected() -> None:
    """Hydra meta in a dict inside a list inside a dict — all forbidden."""
    with pytest.raises(UserParamsRejected, match="_target_"):
        validate_user_params({"a": {"b": [{"c": {"_target_": "evil"}}]}})


def test_list_with_dotted_hydra_meta_in_key_rejected() -> None:
    """Dotted hydra meta in a key inside a list element."""
    with pytest.raises(UserParamsRejected, match="_partial_"):
        validate_user_params({"items": [{"model._partial_": "evil"}]})


def test_list_of_primitives_passes() -> None:
    """Lists of plain values (numbers, strings, bools) are fine."""
    validate_user_params({"layers": [128, 64, 32]})
    validate_user_params({"flags": [True, False]})


def test_nested_list_with_safe_dicts_passes() -> None:
    """Lists of dicts with only safe keys pass."""
    validate_user_params({
        "callbacks": [
            {"name": "early_stop", "patience": 5},
            {"name": "checkpoint", "save_top_k": 3},
        ],
    })
