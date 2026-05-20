"""job_config module-level helpers: compute_idempotency_key + resolve_source_model_path.

These guard the two little pure functions that sit next to ``JobConfigRenderer``
but were uncovered after the Phase 11b test split. Both have subtle invariants
(dict-key ordering, runs:/ URI shape) worth pinning.
"""

from __future__ import annotations

import pytest
from app.services.job_config import (
    _deep_merge,
    _unflatten,
    compute_idempotency_key,
    resolve_source_model_path,
)


def test_idempotency_key_order_stable() -> None:
    """sha256 over a canonical JSON dump must be insensitive to dict key order."""
    k1 = compute_idempotency_key(
        user_id="u1",
        detector_version_id="dv1",
        job_type="train",
        train_ds="ds1",
        test_ds=None,
        predict_ds=None,
        source_model=None,
        params={"a": 1, "b": 2},
    )
    k2 = compute_idempotency_key(
        user_id="u1",
        detector_version_id="dv1",
        job_type="train",
        train_ds="ds1",
        test_ds=None,
        predict_ds=None,
        source_model=None,
        params={"b": 2, "a": 1},  # reversed insertion order
    )
    assert k1 == k2


def test_idempotency_key_differs_per_user() -> None:
    """Two users submitting identical configs must produce distinct keys —
    otherwise user A's submission could de-dup against user B's in the
    idempotency window."""
    k_u1 = compute_idempotency_key(
        user_id="u1",
        detector_version_id="dv1",
        job_type="train",
        train_ds=None,
        test_ds=None,
        predict_ds=None,
        source_model=None,
        params={},
    )
    k_u2 = compute_idempotency_key(
        user_id="u2",
        detector_version_id="dv1",
        job_type="train",
        train_ds=None,
        test_ds=None,
        predict_ds=None,
        source_model=None,
        params={},
    )
    assert k_u1 != k_u2


def test_idempotency_key_differs_per_params() -> None:
    """Same user, same datasets, but different params must hash differently."""
    base = {
        "user_id": "u1",
        "detector_version_id": "dv1",
        "job_type": "train",
        "train_ds": "ds1",
        "test_ds": None,
        "predict_ds": None,
        "source_model": None,
    }
    assert compute_idempotency_key(
        **base, params={"lr": 0.1}
    ) != compute_idempotency_key(**base, params={"lr": 0.2})


def test_resolve_source_model_runs_uri_single_path() -> None:
    assert resolve_source_model_path("runs:/abc123/model") == "model"


def test_resolve_source_model_runs_uri_nested_path() -> None:
    assert resolve_source_model_path("runs:/abc123/model/sub/path") == "model/sub/path"


def test_resolve_source_model_runs_uri_no_subpath() -> None:
    """`runs:/<run_id>` with no trailing artifact path returns the empty string
    — operator may store the run id alone and the renderer must handle it
    without an IndexError."""
    assert resolve_source_model_path("runs:/abc123") == ""


def test_resolve_source_model_rejects_non_runs_uri() -> None:
    with pytest.raises(ValueError, match="runs:/"):
        resolve_source_model_path("s3://bucket/path")


# ---------------------------------------------------------------------------
# Direct exercises of the module-internal helpers. `JobConfigRenderer` never
# routes user_params through `_deep_merge`'s recursive arm (RESERVED_TOP_LEVEL_KEYS
# blocks all overlap with `base`), but the helpers are pure and worth pinning
# directly so a future refactor that introduces a new caller picks them up
# in a correct shape.
# ---------------------------------------------------------------------------


def test_deep_merge_recurses_on_nested_dict_overlap() -> None:
    """Both keys hold dicts → values are merged recursively, not overwritten."""
    dst = {"a": {"b": 1, "c": 2}}
    src = {"a": {"c": 99, "d": 3}}
    result = _deep_merge(dst, src)
    # `c` from src wins; `b` from dst preserved; `d` from src added.
    assert result == {"a": {"b": 1, "c": 99, "d": 3}}


def test_deep_merge_non_dict_value_overwrites():
    """Type mismatch (dict vs non-dict) falls to the override branch — no
    silent coercion."""
    dst = {"a": {"b": 1}}
    src = {"a": "scalar"}
    assert _deep_merge(dst, src) == {"a": "scalar"}


def test_unflatten_descends_into_existing_dict_on_shared_prefix() -> None:
    """Two dotted keys sharing a prefix (`a.b`, `a.c`) reuse the same nested
    dict — covers the `cursor` descent branch in `_unflatten` when `p`
    already exists in the cursor as a dict."""
    result = _unflatten({"a.b": 1, "a.c": 2})
    assert result == {"a": {"b": 1, "c": 2}}


def test_unflatten_preserves_flat_dict_when_no_dotted_overlap() -> None:
    """Flat dict-valued key with no dotted-key collision passes through
    unchanged. Pins the `out[raw_key] = val` arm of the flat-key branch."""
    result = _unflatten({"model": {"n_estimators": 100, "max_depth": 5}})
    assert result == {"model": {"n_estimators": 100, "max_depth": 5}}
