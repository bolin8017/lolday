"""Per-type reference validators on JobCreate.

Each branch of ``JobCreate._validate_refs_per_type`` rejects a specific
illegal combination of dataset / source_model refs for the job type.
These tests pin each error path so accidentally widening the schema
fails loud.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from app.models.job import JobType
from app.schemas.job import JobCreate
from pydantic import ValidationError


def _make(**overrides) -> dict:
    """Build a minimal valid TRAIN payload; overrides replace specific fields."""
    base = {
        "type": JobType.TRAIN,
        "detector_version_id": _uuid.uuid4(),
        "train_dataset_id": _uuid.uuid4(),
    }
    base.update(overrides)
    return base


def test_train_rejects_source_model_version_id() -> None:
    """TRAIN must not carry a source_model_version_id — that field is for
    EVALUATE / PREDICT only."""
    with pytest.raises(ValidationError, match="source_model_version_id must be null"):
        JobCreate(**_make(source_model_version_id=_uuid.uuid4()))


def test_train_rejects_predict_dataset_id() -> None:
    """TRAIN must not carry a predict_dataset_id — TRAIN consumes
    train_dataset_id only."""
    with pytest.raises(ValidationError, match="predict_dataset_id must be null"):
        JobCreate(**_make(predict_dataset_id=_uuid.uuid4()))


def test_evaluate_rejects_missing_test_dataset_id() -> None:
    """EVALUATE requires test_dataset_id."""
    with pytest.raises(ValidationError, match="test_dataset_id required"):
        JobCreate(
            **_make(
                type=JobType.EVALUATE,
                train_dataset_id=None,
                source_model_version_id=_uuid.uuid4(),
            )
        )


def test_evaluate_rejects_train_or_predict_dataset() -> None:
    """EVALUATE accepts only test_dataset_id (with source_model_version_id);
    train_dataset_id or predict_dataset_id is rejected."""
    with pytest.raises(ValidationError, match="only test_dataset_id allowed"):
        JobCreate(
            **_make(
                type=JobType.EVALUATE,
                train_dataset_id=_uuid.uuid4(),
                test_dataset_id=_uuid.uuid4(),
                source_model_version_id=_uuid.uuid4(),
            )
        )
    with pytest.raises(ValidationError, match="only test_dataset_id allowed"):
        JobCreate(
            **_make(
                type=JobType.EVALUATE,
                train_dataset_id=None,
                test_dataset_id=_uuid.uuid4(),
                predict_dataset_id=_uuid.uuid4(),
                source_model_version_id=_uuid.uuid4(),
            )
        )


def test_predict_rejects_missing_predict_dataset_id() -> None:
    """PREDICT requires predict_dataset_id."""
    with pytest.raises(ValidationError, match="predict_dataset_id required"):
        JobCreate(
            **_make(
                type=JobType.PREDICT,
                train_dataset_id=None,
                source_model_version_id=_uuid.uuid4(),
            )
        )


def test_predict_rejects_missing_source_model_version_id() -> None:
    """PREDICT requires source_model_version_id (the model to score with)."""
    with pytest.raises(ValidationError, match="source_model_version_id required"):
        JobCreate(
            **_make(
                type=JobType.PREDICT,
                train_dataset_id=None,
                predict_dataset_id=_uuid.uuid4(),
            )
        )


def test_predict_rejects_train_or_test_dataset() -> None:
    """PREDICT accepts only predict_dataset_id; train_dataset_id or
    test_dataset_id is rejected."""
    with pytest.raises(ValidationError, match="only predict_dataset_id allowed"):
        JobCreate(
            **_make(
                type=JobType.PREDICT,
                train_dataset_id=_uuid.uuid4(),
                predict_dataset_id=_uuid.uuid4(),
                source_model_version_id=_uuid.uuid4(),
            )
        )
    with pytest.raises(ValidationError, match="only predict_dataset_id allowed"):
        JobCreate(
            **_make(
                type=JobType.PREDICT,
                train_dataset_id=None,
                test_dataset_id=_uuid.uuid4(),
                predict_dataset_id=_uuid.uuid4(),
                source_model_version_id=_uuid.uuid4(),
            )
        )


def test_evaluate_happy_path_accepts_test_and_model_only() -> None:
    """EVALUATE with test_dataset_id + source_model_version_id (no train/predict)
    must validate cleanly — pins the fall-through branch from the per-type
    elif into `return self`."""
    obj = JobCreate(
        **_make(
            type=JobType.EVALUATE,
            train_dataset_id=None,
            test_dataset_id=_uuid.uuid4(),
            source_model_version_id=_uuid.uuid4(),
        )
    )
    assert obj.type == JobType.EVALUATE


def test_predict_happy_path_accepts_predict_and_model_only() -> None:
    """PREDICT with predict_dataset_id + source_model_version_id (no train/test)
    must validate cleanly."""
    obj = JobCreate(
        **_make(
            type=JobType.PREDICT,
            train_dataset_id=None,
            predict_dataset_id=_uuid.uuid4(),
            source_model_version_id=_uuid.uuid4(),
        )
    )
    assert obj.type == JobType.PREDICT
