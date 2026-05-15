"""Phase 5 — per-job active_deadline_seconds override."""

from __future__ import annotations

import uuid

import pytest
from app.models.job import JobType
from app.schemas.job import JobCreate
from pydantic import ValidationError


def _base_kwargs(jt: JobType = JobType.TRAIN) -> dict:
    base: dict = {
        "type": jt,
        "detector_version_id": uuid.uuid4(),
    }
    if jt == JobType.TRAIN:
        base["train_dataset_id"] = uuid.uuid4()
    elif jt == JobType.EVALUATE:
        base["test_dataset_id"] = uuid.uuid4()
        base["source_model_version_id"] = uuid.uuid4()
    else:
        base["predict_dataset_id"] = uuid.uuid4()
        base["source_model_version_id"] = uuid.uuid4()
    return base


def test_active_deadline_default_is_none() -> None:
    job = JobCreate(**_base_kwargs())
    assert job.active_deadline_seconds is None


def test_active_deadline_override_accepted_under_cap() -> None:
    job = JobCreate(**_base_kwargs(), active_deadline_seconds=43200)  # 12h
    assert job.active_deadline_seconds == 43200


def test_active_deadline_above_cap_rejected() -> None:
    with pytest.raises(ValidationError, match="exceeds max"):
        JobCreate(**_base_kwargs(), active_deadline_seconds=100000)  # > 24h


def test_active_deadline_zero_rejected() -> None:
    with pytest.raises(ValidationError, match="must be > 0"):
        JobCreate(**_base_kwargs(), active_deadline_seconds=0)


def test_active_deadline_evaluate_cap_is_lower() -> None:
    """Evaluate cap is 7200 (2h); 8000 is above evaluate cap but below
    train cap — the validator must use the per-type MAX."""
    with pytest.raises(ValidationError, match="exceeds max"):
        JobCreate(
            **_base_kwargs(JobType.EVALUATE),
            active_deadline_seconds=8000,
        )
