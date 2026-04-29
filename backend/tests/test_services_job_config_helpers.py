"""job_config module-level helpers: compute_idempotency_key + resolve_source_model_path.

These guard the two little pure functions that sit next to ``JobConfigRenderer``
but were uncovered after the Phase 11b test split. Both have subtle invariants
(dict-key ordering, runs:/ URI shape) worth pinning.
"""

from __future__ import annotations

import pytest
from app.services.job_config import compute_idempotency_key, resolve_source_model_path


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


def test_resolve_source_model_rejects_non_runs_uri() -> None:
    with pytest.raises(ValueError, match="runs:/"):
        resolve_source_model_path("s3://bucket/path")
