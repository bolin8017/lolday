"""Phase 11b job-submission pre-flight: manifest + dataset + resource compatibility."""

from __future__ import annotations

import pytest
from maldet.manifest import DetectorManifest

from app.models.job import ResourceProfile
from app.services.validator import (
    JobSubmissionError,
    validate_job_submission,
)


def _manifest(**overrides) -> DetectorManifest:
    data = {
        "detector": {"name": "d", "version": "1", "framework": "sklearn"},
        "input": {"binary_format": "elf", "required_sections": [], "dataset_contract": "sample_csv"},
        "output": {"task": "binary_classification", "classes": ["Malware", "Benign"], "score_range": [0.0, 1.0]},
        "resources": {"supports": ["cpu"], "recommended": "cpu", "min_memory_gib": 1, "gpu_required": False},
        "lifecycle": {
            "stages": ["train", "evaluate", "predict"],
            "supports_serving": False, "supports_hpsweep": True,
            "supports_distributed": False, "supports_multinode": False,
        },
        "artifacts": {
            "model": {"path": "model/", "type": "dir"},
            "metrics": {"path": "metrics.json", "type": "file"},
            "predictions": {"path": "predictions.csv", "type": "file"},
        },
        "compat": {"min_python": "3.12", "min_maldet": "1.0", "schema_version": 1},
        "stages": {},
    }
    data.update(overrides)
    return DetectorManifest.model_validate(data)


def test_accepts_supported_profile() -> None:
    m = _manifest()
    validate_job_submission(
        manifest=m,
        resource_profile=ResourceProfile.STANDARD,
        dataset_contract="sample_csv",
        stage="train",
    )


def test_rejects_unsupported_profile() -> None:
    m = _manifest()
    with pytest.raises(JobSubmissionError, match="resource_profile"):
        validate_job_submission(
            manifest=m,
            resource_profile=ResourceProfile.GPU2,
            dataset_contract="sample_csv",
            stage="train",
        )


def test_rejects_mismatched_dataset_contract() -> None:
    m = _manifest()
    with pytest.raises(JobSubmissionError, match="dataset_contract"):
        validate_job_submission(
            manifest=m,
            resource_profile=ResourceProfile.STANDARD,
            dataset_contract="sample_jsonl",
            stage="train",
        )


def test_rejects_stage_not_declared() -> None:
    m = _manifest(lifecycle={
        "stages": ["train", "evaluate"],
        "supports_serving": False, "supports_hpsweep": True,
        "supports_distributed": False, "supports_multinode": False,
    })
    with pytest.raises(JobSubmissionError, match="stage"):
        validate_job_submission(
            manifest=m,
            resource_profile=ResourceProfile.STANDARD,
            dataset_contract="sample_csv",
            stage="predict",
        )
