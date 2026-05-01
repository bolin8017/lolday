"""Read-model projections from job_events into Job.summary_metrics.

Phase 11e introduced ``Job.summary_metrics`` as a single-writer materialized
read-model populated on stage_end. The two projectors:

- :func:`_project_summary_metrics` aggregates last-per-name ``metric``,
  ``confusion_matrix``, and ``per_class`` events.
- :func:`_project_prediction_summary` reads ``predictions.csv`` from the
  succeeded predict job's MLflow run and computes a class-distribution
  summary cached under ``summary_metrics["prediction_summary"]``.

Both projectors are idempotent: running twice produces the same result.
Errors are logged + counted via ``BACKEND_ERRORS`` and never raised — the
projection is opportunistic, not part of the state-machine transition.
"""

import csv
import io
import logging
import uuid
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.job import Job

logger = logging.getLogger(__name__)


async def _project_summary_metrics(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Aggregate last-per-name metric events + latest confusion_matrix event for
    ``job_id`` into ``Job.summary_metrics``. Idempotent — running twice produces
    the same result.

    Phase 11e: ``job_events`` is the canonical source of truth for run-time
    metrics; ``Job.summary_metrics`` is a single-writer materialized read model
    populated here on stage_end. MLflow remains the long-term store but is no
    longer the authoritative source for the lolday UI summary card.
    Phase 13b: adds per_class (from BinaryClassification.evaluate emit).
    """
    from app.models import JobEvent

    rows = (
        await session.execute(
            select(JobEvent.kind, JobEvent.payload, JobEvent.ts)
            .where(JobEvent.job_id == job_id)
            .where(JobEvent.kind.in_(["metric", "confusion_matrix", "per_class"]))
            .order_by(JobEvent.ts.asc())
        )
    ).all()

    metrics: dict[str, float] = {}
    confusion_matrix: dict[str, Any] | None = None
    per_class: dict[str, Any] | None = None
    for kind, payload, _ts in rows:
        if kind == "metric":
            try:
                metrics[payload["name"]] = float(payload["value"])
            except (KeyError, TypeError, ValueError):
                continue
        elif kind == "confusion_matrix":
            try:
                confusion_matrix = {
                    "labels": payload["labels"],
                    "matrix": payload["matrix"],
                }
            except KeyError:
                continue
        elif kind == "per_class":
            payload_per_class = payload.get("per_class")
            if isinstance(payload_per_class, dict):
                per_class = payload_per_class

    job = await session.get(Job, job_id)
    if job is None:
        raise RuntimeError(
            f"FK invariant violated: _project_summary_metrics called with unknown job_id {job_id}"
        )
    job.summary_metrics = {
        "metrics": metrics,
        "confusion_matrix": confusion_matrix,
        "per_class": per_class,
    }
    await session.commit()


async def _read_mlflow_artifact(run_id: str, path: str) -> str:
    """Fetch an MLflow artifact text body via the tracking server proxy.

    Returns the raw text content. Raises ``FileNotFoundError`` on 404 so
    the caller can decide whether to skip silently (predict jobs that
    legitimately lack a ``predictions.csv`` should not surface as an
    error).
    """
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/runs/get?run_id={run_id}"
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        run_resp = await c.get(url)
        run_resp.raise_for_status()
        artifact_uri: str = run_resp.json()["run"]["info"]["artifact_uri"]

    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise RuntimeError(f"unexpected artifact_uri scheme: {artifact_uri!r}")
    relative = artifact_uri[len(prefix) :].rstrip("/")
    download_url = (
        f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/"
        f"{relative}/{path}"
    )
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(download_url)
    if r.status_code == 404:
        raise FileNotFoundError(path)
    r.raise_for_status()
    return r.text


async def _project_prediction_summary(session: AsyncSession, j: Job) -> None:
    """Read predictions.csv via MLflow artifacts on a succeeded predict job,
    compute total + class-distribution + duration, cache into
    ``Job.summary_metrics["prediction_summary"]``.

    Errors are logged + counted via ``BACKEND_ERRORS`` and never raised —
    projection failure is observability tech debt, not a state-machine
    issue. Job remains SUCCEEDED; the frontend falls back to a recompute
    endpoint (Task 1.3) when the cache is absent.
    """
    if not j.mlflow_run_id:
        return
    try:
        csv_text = await _read_mlflow_artifact(j.mlflow_run_id, "predictions.csv")
    except FileNotFoundError:
        return
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_artifact_read").inc()
        logger.exception(
            "prediction_summary artifact read failed",
            extra={"job_id": str(j.id)},
        )
        return

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
    except csv.Error:
        BACKEND_ERRORS.labels(stage="prediction_summary_csv_parse").inc()
        logger.exception(
            "prediction_summary csv parse failed",
            extra={"job_id": str(j.id)},
        )
        return

    # maldet binary-classification evaluator writes `pred_label` per the
    # framework's prediction-CSV contract (alongside pred_score and per-class
    # probabilities). Detectors that emit a non-standard CSV are silently
    # skipped — better to render no card than wrong counts.
    if not reader.fieldnames or "pred_label" not in reader.fieldnames:
        return
    distribution = Counter(row["pred_label"] for row in rows)
    total = len(rows)
    duration_seconds = (
        (j.finished_at - j.started_at).total_seconds()
        if (j.started_at and j.finished_at)
        else None
    )

    sm = dict(j.summary_metrics or {})
    sm["prediction_summary"] = {
        "total": total,
        "distribution": {str(k): int(v) for k, v in distribution.items()},
        "duration_seconds": duration_seconds,
    }
    j.summary_metrics = sm
    await session.commit()
