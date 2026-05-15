"""JobSummary exposes summary_metrics — phase 11e."""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid

from app.schemas.job import JobSummary


def _base_payload() -> dict:
    """Minimal payload with all required JobSummary fields."""
    return {
        "id": _uuid.uuid4(),
        "type": "train",
        "status": "succeeded",
        "detector_version_id": _uuid.uuid4(),
        "owner_id": _uuid.uuid4(),
        "mlflow_run_id": None,
        "k8s_job_name": None,
        "failure_reason": None,
        "submitted_at": _dt.datetime.now(_dt.UTC),
        "started_at": None,
        "finished_at": None,
    }


def test_job_summary_has_summary_metrics_field() -> None:
    assert "summary_metrics" in JobSummary.model_fields


def test_job_summary_accepts_null_summary_metrics() -> None:
    """Jobs that haven't reached stage_end yet (or have no metric events) get None."""
    base = _base_payload()
    base["summary_metrics"] = None
    obj = JobSummary.model_validate(base)
    assert obj.summary_metrics is None


def test_job_summary_accepts_populated_summary_metrics() -> None:
    base = _base_payload()
    base["summary_metrics"] = {
        "metrics": {"acc": 0.987, "f1": 0.94},
        "confusion_matrix": {"labels": ["a", "b"], "matrix": [[1, 0], [0, 1]]},
    }
    obj = JobSummary.model_validate(base)
    assert obj.summary_metrics is not None
    assert obj.summary_metrics["metrics"]["acc"] == 0.987


def test_job_summary_summary_metrics_default_none() -> None:
    """If summary_metrics isn't provided, it should default to None (not raise)."""
    base = _base_payload()
    obj = JobSummary.model_validate(base)
    assert obj.summary_metrics is None
