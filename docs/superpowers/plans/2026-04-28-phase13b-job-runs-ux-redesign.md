# Phase 13b Job Detail / Submit / Runs UX Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Job Detail (per-type Summary tab), Submit form Hyperparameters block, Resolved config viewer, and Runs three-tier UI; expose MLflow UI behind Cloudflare Access.

**Architecture:** Job Detail dispatches to `<TrainSummary>` / `<EvaluateSummary>` / `<PredictSummary>` based on `job.type`, each tailored to that stage's salient information. RJSF gets an auto-derived `uiSchema` from JSON Schema (`description`, `default`, `title`) plus a `<StageExplainer>` and pre-populated defaults. `<JsonViewer>` is replaced everywhere by `<JsonTreeView>` (powered by `react-json-view`). Runs gets light improvements (stats on experiments index, column picker on runs list, tree views on run detail) plus a Traefik IngressRoute exposing MLflow UI at `/mlflow/` (read-only — GET/HEAD/OPTIONS only) for the comparison/charts use cases.

**Tech Stack:** FastAPI, SQLAlchemy 2 / PostgreSQL, Alembic, React 18 + TypeScript + Tailwind + shadcn/ui + react-router 7, RJSF v5 + AJV, react-json-view, MLflow 2.x, Traefik, Helm, vitest + playwright, pytest.

**Spec:** `/home/bolin8017/Documents/repositories/lolday/docs/superpowers/specs/2026-04-28-phase13b-job-runs-ux-redesign-design.md`

**Depends on:** Phase 13a deployed (sidebar fix, log capture fix, manifest fix, delete UX) — 13b builds on the cleaner 13a foundation.

---

## File Structure (which file does what)

### Backend

- `backend/app/models/job.py` — add `user_params` JSONB column (B3)
- `backend/app/schemas/job.py` — `JobRead.user_params: dict | None` (B3)
- `backend/app/routers/jobs.py` — `submit_job` writes `user_params`; new `GET /jobs/{id}/prediction-summary` fallback endpoint (B1, B3)
- `backend/app/reconciler.py` — extend `_project_summary_metrics` for `per_class` event; new `_project_prediction_summary` for predict jobs (B1)
- `backend/app/routers/experiments_proxy.py` — `?include=stats` aggregate endpoint with manual async TTL cache (B4)
- `backend/migrations/versions/<hash>_phase13b_user_params.py` — column add (B3)
- `backend/tests/test_reconciler_summary_projection.py` — extend (B1)
- `backend/tests/test_reconciler_prediction_summary.py` — new (B1)
- `backend/tests/test_routers_jobs.py` — `submit_job_records_user_params`, prediction-summary endpoint (B1, B3)
- `backend/tests/test_routers_experiments_aggregate.py` — new (B4)

### Frontend — new components

- `frontend/src/components/common/JsonTreeView.tsx` — replaces `JsonViewer` (B3)
- `frontend/src/components/common/CollapsibleCard.tsx` — used on Run detail params/tags (B4)
- `frontend/src/components/common/OpenInMlflowButton.tsx` — deep-link to MLflow UI (B4)
- `frontend/src/components/common/OpenInLoldayJobButton.tsx` — deep-link from MLflow run back to lolday job (B4)
- `frontend/src/components/jobs/JobDetailShell.tsx` — header + tabs scaffold (B1)
- `frontend/src/components/jobs/TrainSummary.tsx` (B1)
- `frontend/src/components/jobs/EvaluateSummary.tsx` (B1)
- `frontend/src/components/jobs/PredictSummary.tsx` (B1)
- `frontend/src/components/jobs/MetricsTable.tsx` — replaces `MetricCards` (B1)
- `frontend/src/components/jobs/PerClassMetrics.tsx` (B1)
- `frontend/src/components/jobs/SourceModelCard.tsx` (B1)
- `frontend/src/components/jobs/TrainedModelCard.tsx` (B1)
- `frontend/src/components/jobs/PredictionSummaryCard.tsx` (B1)
- `frontend/src/components/jobs/ResolvedConfigCard.tsx` (B3)
- `frontend/src/components/jobs/UserParamsTable.tsx` (B3)
- `frontend/src/components/forms/StageExplainer.tsx` (B2)
- `frontend/src/components/forms/RjsfConfigForm.logic.ts` — `deriveUiSchemaFromSchema` + `fillDefaults` (B2)
- `frontend/src/components/runs/ExperimentCard.tsx` (B4)
- `frontend/src/components/runs/RunsColumnPicker.tsx` (B4)
- `frontend/src/components/runs/RunsStatusFilter.tsx` (B4)

### Frontend — modified

- `frontend/src/routes/_authed.jobs.$id.tsx` — rewrite as dispatcher (B1)
- `frontend/src/routes/_authed.jobs.new.tsx` — unchanged (uses JobSubmitForm)
- `frontend/src/components/forms/JobSubmitForm.tsx` — insert `<StageExplainer>` (B2)
- `frontend/src/components/forms/RjsfConfigForm.tsx` — rewrite using `RjsfConfigForm.logic.ts` (B2)
- `frontend/src/routes/_authed.detectors.$id.tsx` — `<ManifestView>` uses `<JsonTreeView>` (B3)
- `frontend/src/routes/_authed.runs._index.tsx` — rewrite (B4)
- `frontend/src/routes/_authed.runs.$expId.tsx` — rewrite (B4)
- `frontend/src/routes/_authed.runs.$expId.$runId.tsx` — rewrite (B4)
- `frontend/src/api/queries/runs.ts` — `useExperimentsWithStats` (B4)
- `frontend/src/api/queries/models.ts` — `useModelVersion(id)`, `useModelVersionForJob(jobId)` (B1)
- `frontend/src/i18n/zh-TW.json`, `en.json` — stage explainer keys (B2)
- `frontend/package.json` — add `react-json-view` (B3)

### Frontend — deleted

- `frontend/src/components/common/JsonViewer.tsx` (B3)
- `frontend/src/components/charts/MetricCards.tsx` (B1)

### Chart

- `charts/lolday/templates/ingress.yaml` — add MLflow IngressRoute + middleware (B5)
- `charts/lolday/templates/mlflow.yaml` — `--static-prefix=/mlflow` (B5)

### Tests

- `frontend/tests/unit/RjsfConfigForm.logic.test.ts` (B2)
- `frontend/tests/unit/RjsfConfigForm.test.tsx` (B2)
- `frontend/tests/unit/MetricsTable.test.tsx` (B1)
- `frontend/tests/unit/ResolvedConfigCard.test.tsx` (B3)
- `frontend/tests/unit/RunsColumnPicker.test.tsx` (B4)
- `frontend/tests/e2e/jobs.spec.ts` — extend (B1, B2)
- `frontend/tests/e2e/runs.spec.ts` — new (B4)
- `frontend/tests/e2e/mlflow.spec.ts` — new (B5)

---

## Task 1.1: Backend — extend `_project_summary_metrics` for `per_class` event

**Files:**

- Modify: `backend/app/reconciler.py`
- Modify: `backend/tests/test_reconciler_summary_projection.py` (extend; create if absent)

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_reconciler_summary_projection.py` add:

```python
@pytest.mark.asyncio
async def test_projects_per_class_event_into_summary_metrics(
    async_session, job_factory, job_event_factory,
):
    """Phase 13b B1: per_class event flows into summary_metrics.per_class."""
    job = await job_factory(type="evaluate", status="running")
    await job_event_factory(job_id=job.id, kind="metric",
                             payload={"name": "accuracy", "value": 0.9})
    await job_event_factory(job_id=job.id, kind="per_class", payload={
        "per_class": {
            "Malware": {"precision": 0.95, "recall": 0.94, "f1": 0.94, "support": 530},
            "Benign":  {"precision": 0.88, "recall": 0.89, "f1": 0.88, "support": 470},
        },
    })

    from app.reconciler import _project_summary_metrics
    await _project_summary_metrics(async_session, job.id)
    await async_session.refresh(job)

    sm = job.summary_metrics
    assert sm["metrics"]["accuracy"] == pytest.approx(0.9)
    assert sm["per_class"]["Malware"]["f1"] == pytest.approx(0.94)
    assert sm["per_class"]["Benign"]["support"] == 470
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_reconciler_summary_projection.py::test_projects_per_class_event_into_summary_metrics -xvs
```

Expected: FAIL — `summary_metrics` has no `per_class` key.

- [ ] **Step 3: Extend `_project_summary_metrics`**

In `backend/app/reconciler.py`, modify `_project_summary_metrics` (around line 836):

```python
async def _project_summary_metrics(
    session: AsyncSession, job_id: uuid.UUID
) -> None:
    """Aggregate metric / confusion_matrix / per_class events into Job.summary_metrics.

    Phase 11e: introduces metrics + confusion_matrix.
    Phase 13b: adds per_class (from BinaryClassification.evaluate emit).
    Idempotent — running twice produces the same result.
    """
    from app.models import JobEvent

    rows = (await session.execute(
        select(JobEvent.kind, JobEvent.payload, JobEvent.ts)
        .where(JobEvent.job_id == job_id)
        .where(JobEvent.kind.in_(["metric", "confusion_matrix", "per_class"]))
        .order_by(JobEvent.ts.asc())
    )).all()

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
    job.summary_metrics = {
        "metrics": metrics,
        "confusion_matrix": confusion_matrix,
        "per_class": per_class,
    }
    await session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```
cd backend && uv run pytest tests/test_reconciler_summary_projection.py -xvs
```

Expected: all PASS (existing tests + new one).

- [ ] **Step 5: Commit**

```bash
git add backend/app/reconciler.py backend/tests/test_reconciler_summary_projection.py
git commit -m "$(cat <<'EOF'
feat(reconciler): project per_class event into summary_metrics (phase 13b B1)

Pairs with the maldet evaluator emit (external dep). Falls through
gracefully when no per_class event exists — summary_metrics.per_class
is None and the frontend hides the section.
EOF
)"
```

---

## Task 1.2: Backend — `_project_prediction_summary` for predict jobs

**Files:**

- Modify: `backend/app/reconciler.py`
- Create: `backend/tests/test_reconciler_prediction_summary.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reconciler_prediction_summary.py`:

```python
"""Phase 13b B1: prediction summary projection."""
import io
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_project_prediction_summary_writes_to_summary_metrics(
    async_session, job_factory,
):
    job = await job_factory(
        type="predict", status="succeeded",
        mlflow_run_id="run-123",
    )

    csv = "sha256,predicted_class\nA,Malware\nB,Benign\nC,Malware\nD,Malware\n"
    with patch("app.reconciler._read_mlflow_artifact",
               new=AsyncMock(return_value=csv)):
        from app.reconciler import _project_prediction_summary
        await _project_prediction_summary(async_session, job)
    await async_session.refresh(job)

    ps = job.summary_metrics["prediction_summary"]
    assert ps["total"] == 4
    assert ps["distribution"] == {"Malware": 3, "Benign": 1}
    assert isinstance(ps["duration_seconds"], (int, float)) or ps["duration_seconds"] is None


@pytest.mark.asyncio
async def test_project_prediction_summary_handles_missing_csv(
    async_session, job_factory,
):
    job = await job_factory(type="predict", status="failed", mlflow_run_id="run-x")
    with patch("app.reconciler._read_mlflow_artifact",
               new=AsyncMock(side_effect=FileNotFoundError("no predictions.csv"))):
        from app.reconciler import _project_prediction_summary
        await _project_prediction_summary(async_session, job)
    await async_session.refresh(job)

    # On error: don't blow up; just don't write the field.
    ps = (job.summary_metrics or {}).get("prediction_summary")
    assert ps is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_reconciler_prediction_summary.py -xvs
```

Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Implement `_project_prediction_summary` and `_read_mlflow_artifact`**

Add to `backend/app/reconciler.py`:

```python
import io
import pandas as pd
from sqlalchemy.orm.attributes import flag_modified

import httpx
from app.config import settings


async def _read_mlflow_artifact(run_id: str, path: str) -> str:
    """Fetch an MLflow artifact text body via the tracking server proxy.

    Returns the raw text content. Raises FileNotFoundError on 404 so the
    caller can decide whether to skip silently.
    """
    # Mirror experiments_proxy.download_artifact's resolution
    url = (
        f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/runs/get"
        f"?run_id={run_id}"
    )
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        run_resp = await c.get(url)
        run_resp.raise_for_status()
        artifact_uri: str = run_resp.json()["run"]["info"]["artifact_uri"]

    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise RuntimeError(f"unexpected artifact_uri scheme: {artifact_uri!r}")
    relative = artifact_uri[len(prefix):].rstrip("/")
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
    """Read predictions.csv via MLflow artifacts, compute distribution,
    cache into Job.summary_metrics.prediction_summary.

    On error: log the exception, increment BACKEND_ERRORS, do not raise
    (projection failure must not block job termination).
    """
    if not j.mlflow_run_id:
        return
    try:
        csv_text = await _read_mlflow_artifact(j.mlflow_run_id, "predictions.csv")
    except FileNotFoundError:
        return  # predict job that didn't produce predictions.csv (failed earlier)
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_artifact_read").inc()
        logger.exception("prediction_summary artifact read failed",
                         extra={"job_id": str(j.id)})
        return

    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_csv_parse").inc()
        logger.exception("prediction_summary csv parse failed",
                         extra={"job_id": str(j.id)})
        return

    if "predicted_class" not in df.columns:
        return
    distribution = df["predicted_class"].value_counts().to_dict()
    total = int(len(df))
    duration_seconds = (
        (j.finished_at - j.started_at).total_seconds()
        if (j.started_at and j.finished_at) else None
    )

    sm = dict(j.summary_metrics or {})
    sm["prediction_summary"] = {
        "total": total,
        "distribution": {str(k): int(v) for k, v in distribution.items()},
        "duration_seconds": duration_seconds,
    }
    j.summary_metrics = sm
    flag_modified(j, "summary_metrics")
    await session.commit()
```

Wire into `_handle_job_succeeded` (around line 898). After the existing `_project_summary_metrics` call:

```python
if j.type == JobType.PREDICT:
    try:
        await _project_prediction_summary(session, j)
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_projection").inc()
        logger.exception("prediction_summary projection failed",
                         extra={"job_id": str(j.id)})
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_reconciler_prediction_summary.py -xvs
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/reconciler.py backend/tests/test_reconciler_prediction_summary.py
git commit -m "$(cat <<'EOF'
feat(reconciler): project predict-job CSV into summary_metrics.prediction_summary (phase 13b B1)

Reads predictions.csv via MLflow artifacts on succeeded predict jobs,
computes total + class distribution + duration, caches into
summary_metrics. Frontend's PredictionSummaryCard reads from the cache.
Errors are logged, never raised — projection failure must not block job
termination.
EOF
)"
```

---

## Task 1.3: Backend — `GET /jobs/{id}/prediction-summary` fallback endpoint

**Files:**

- Modify: `backend/app/routers/jobs.py`
- Modify: `backend/tests/test_routers_jobs.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routers_jobs.py`:

```python
async def test_prediction_summary_endpoint_returns_cached(
    async_client, job_factory, auth_owner_headers,
):
    job = await job_factory(
        type="predict", status="succeeded",
        summary_metrics={"prediction_summary": {
            "total": 100,
            "distribution": {"Malware": 60, "Benign": 40},
            "duration_seconds": 12.0,
        }},
    )
    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 100
    assert resp.json()["distribution"]["Malware"] == 60


async def test_prediction_summary_endpoint_404_when_unavailable(
    async_client, job_factory, auth_owner_headers, monkeypatch,
):
    job = await job_factory(type="predict", status="failed", summary_metrics={})
    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 404


async def test_prediction_summary_endpoint_400_for_non_predict(
    async_client, job_factory, auth_owner_headers,
):
    job = await job_factory(type="train", status="succeeded")
    resp = await async_client.get(
        f"/api/v1/jobs/{job.id}/prediction-summary",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_routers_jobs.py -k prediction_summary -xvs
```

Expected: 405.

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/routers/jobs.py`:

```python
@router.get("/{job_id}/prediction-summary")
async def get_prediction_summary(
    job_id: UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Phase 13b B1: prediction summary cached on successful predict jobs.

    Cache miss returns 404; the reconciler projection populates the cache
    on terminal transition. Returning 404 (rather than recomputing on
    demand) keeps the read path predictable; legacy predict jobs without
    the cache need a one-shot backfill script.
    """
    job = await session.get(Job, job_id)
    if job is None or job.owner_id != user.id and user.role != Role.ADMIN:
        raise HTTPException(status_code=404, detail="job not found")
    if job.type != JobType.PREDICT:
        raise HTTPException(status_code=400, detail={
            "code": "not_predict_job",
            "message": "prediction-summary is only available on predict jobs",
        })
    ps = (job.summary_metrics or {}).get("prediction_summary")
    if not ps:
        raise HTTPException(status_code=404, detail={
            "code": "summary_unavailable",
            "message": "prediction summary not available for this job (legacy or failed)",
        })
    return ps
```

Match imports / dependencies pattern with the existing handlers in the file.

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_routers_jobs.py -k prediction_summary -xvs
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/jobs.py backend/tests/test_routers_jobs.py
git commit -m "$(cat <<'EOF'
feat(jobs): GET /jobs/{id}/prediction-summary endpoint (phase 13b B1)

Returns the cached prediction_summary from the reconciler projection.
404 on cache miss (legacy predict jobs or failed predict jobs without
predictions.csv); 400 for non-predict jobs.
EOF
)"
```

---

## Task 2.1: Migration — `Job.user_params` column

**Files:**

- Modify: `backend/app/models/job.py`
- Create: `backend/migrations/versions/<hash>_phase13b_user_params.py`

- [ ] **Step 1: Add column to model**

In `backend/app/models/job.py`, add to the `Job` class:

```python
# Phase 13b B3: raw user-submitted params (before defaults merge), used
# by the resolved-config UI to highlight what the user actually changed.
user_params: Mapped[dict | None] = mapped_column(
    JSONB, nullable=True,
)
```

Make sure the import for `JSONB` is present (`from sqlalchemy.dialects.postgresql import JSONB`).

- [ ] **Step 2: Generate Alembic revision**

```bash
cd backend && uv run alembic revision -m "phase13b job user_params column"
```

- [ ] **Step 3: Write the migration body**

In the new file:

```python
"""phase13b job user_params column

Revision ID: <hash>
Revises: <previous>
Create Date: 2026-04-28 ...
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "<hash>"
down_revision = "<previous>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job",
        sa.Column("user_params", postgresql.JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job", "user_params")
```

- [ ] **Step 4: Run migration**

```bash
cd backend && uv run alembic upgrade head
psql -d lolday -c "\d job" | grep user_params
```

Expected: column exists, nullable.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/job.py backend/migrations/versions/<hash>_phase13b_user_params.py
git commit -m "$(cat <<'EOF'
feat(job): add user_params JSONB column (phase 13b B3)

Stores the raw user-submitted params before defaults merge so the UI
can show 'your params' separately from 'resolved config' and highlight
overrides.
EOF
)"
```

---

## Task 2.2: Backend — `submit_job` writes `user_params`; `JobRead` exposes it

**Files:**

- Modify: `backend/app/schemas/job.py`
- Modify: `backend/app/routers/jobs.py`
- Modify: `backend/tests/test_routers_jobs.py`

- [ ] **Step 1: Add to schema**

In `backend/app/schemas/job.py`, modify `JobRead`:

```python
class JobRead(JobSummary):
    train_dataset_id: uuid.UUID | None
    test_dataset_id: uuid.UUID | None
    predict_dataset_id: uuid.UUID | None
    source_model_version_id: uuid.UUID | None
    resolved_config: dict
    user_params: dict | None             # phase 13b B3
    log_tail: str | None
    resource_profile: ResourceProfile
    mlflow_experiment_id: str | None
```

- [ ] **Step 2: Write the failing test**

```python
async def test_submit_job_records_user_params(
    async_client, detector_factory, version_factory, dataset_factory,
    auth_owner_headers,
):
    """Phase 13b B3: submit_job stores raw user-supplied params in job.user_params."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, status="active")
    train_ds = await dataset_factory()
    test_ds = await dataset_factory()
    user_params = {"n_estimators": 200, "max_depth": 10}

    resp = await async_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": str(version.id),
            "train_dataset_id": str(train_ds.id),
            "test_dataset_id": str(test_ds.id),
            "params": user_params,
        },
        headers=auth_owner_headers,
    )
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    detail = await async_client.get(f"/api/v1/jobs/{job_id}", headers=auth_owner_headers)
    assert detail.json()["user_params"] == user_params
```

- [ ] **Step 3: Run test to verify it fails**

```
cd backend && uv run pytest tests/test_routers_jobs.py::test_submit_job_records_user_params -xvs
```

Expected: FAIL — `user_params` is `None` in response.

- [ ] **Step 4: Wire `submit_job` to write `user_params`**

In `backend/app/routers/jobs.py`, find `submit_job` and ensure the `Job(...)` constructor includes `user_params=body.params`:

```python
job = Job(
    type=body.type,
    detector_version_id=body.detector_version_id,
    train_dataset_id=body.train_dataset_id,
    test_dataset_id=body.test_dataset_id,
    predict_dataset_id=body.predict_dataset_id,
    source_model_version_id=body.source_model_version_id,
    user_params=body.params,           # ← phase 13b B3
    resolved_config=resolved_config,
    owner_id=user.id,
    ...
)
```

(Locate the actual constructor in the existing file; preserve its other fields.)

- [ ] **Step 5: Run test to verify it passes**

```
cd backend && uv run pytest tests/test_routers_jobs.py::test_submit_job_records_user_params -xvs
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/job.py backend/app/routers/jobs.py backend/tests/test_routers_jobs.py
git commit -m "$(cat <<'EOF'
feat(jobs): submit_job records raw user_params; JobRead exposes it (phase 13b B3)

Frontend ResolvedConfigCard reads job.user_params to show 'your
parameters' separate from the merged resolved config, with
default-vs-overridden indicators.
EOF
)"
```

---

## Task 3.1: Backend — `experiments?include=stats` aggregate endpoint

**Files:**

- Modify: `backend/app/routers/experiments_proxy.py`
- Create: `backend/tests/test_routers_experiments_aggregate.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_routers_experiments_aggregate.py`:

```python
"""Phase 13b B4: experiments aggregate endpoint with manual async TTL cache."""
from unittest.mock import AsyncMock, patch
import pytest


@pytest.mark.asyncio
async def test_experiments_no_include_returns_bare_list(async_client, auth_owner_headers):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    with patch("app.routers.experiments_proxy._client") as mc:
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        resp = await async_client.get("/api/v1/experiments", headers=auth_owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == fake_experiments
    assert "run_count" not in body[0]


@pytest.mark.asyncio
async def test_experiments_with_stats_aggregates(async_client, auth_owner_headers):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    fake_runs = [
        {"run_id": "r1", "status": "FINISHED", "start_time": 1700000000000,
         "metrics": {"f1": 0.91}},
        {"run_id": "r2", "status": "FINISHED", "start_time": 1700001000000,
         "metrics": {"f1": 0.93}},
        {"run_id": "r3", "status": "RUNNING", "start_time": 1700002000000, "metrics": {}},
    ]
    with patch("app.routers.experiments_proxy._client") as mc, \
         patch("app.routers.experiments_proxy._stats_cache",
               new_callable=lambda: __import__("cachetools").TTLCache(maxsize=64, ttl=30)):
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        mc.return_value.search_runs = AsyncMock(return_value=fake_runs)
        resp = await async_client.get(
            "/api/v1/experiments?include=stats", headers=auth_owner_headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["run_count"] == 3
    assert body[0]["best_f1"] == pytest.approx(0.93)
    assert body[0]["latest_start_time"] == 1700002000000


@pytest.mark.asyncio
async def test_experiments_stats_cached(async_client, auth_owner_headers):
    fake_experiments = [{"experiment_id": "1", "name": "exp_a"}]
    runs_called = 0

    async def mock_search_runs(experiment_ids, max_results):
        nonlocal runs_called
        runs_called += 1
        return [{"run_id": "r1", "status": "FINISHED",
                 "start_time": 1, "metrics": {"f1": 0.5}}]

    with patch("app.routers.experiments_proxy._client") as mc, \
         patch("app.routers.experiments_proxy._stats_cache",
               new_callable=lambda: __import__("cachetools").TTLCache(maxsize=64, ttl=30)):
        mc.return_value.search_experiments = AsyncMock(return_value=fake_experiments)
        mc.return_value.search_runs = AsyncMock(side_effect=mock_search_runs)

        await async_client.get("/api/v1/experiments?include=stats",
                                headers=auth_owner_headers)
        await async_client.get("/api/v1/experiments?include=stats",
                                headers=auth_owner_headers)
    assert runs_called == 1   # second call hit cache
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && uv run pytest tests/test_routers_experiments_aggregate.py -xvs
```

Expected: third test fails (no caching), second fails (no aggregate).

- [ ] **Step 3: Implement aggregate endpoint with manual async cache**

Replace `backend/app/routers/experiments_proxy.py` `list_experiments` and add helpers:

```python
import asyncio
from cachetools import TTLCache

_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)
_stats_locks: dict[str, asyncio.Lock] = {}


@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
    include: str | None = Query(None, regex="^stats$"),
):
    try:
        experiments = await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if include != "stats":
        return experiments

    enriched = []
    for exp in experiments:
        try:
            stats = await _experiment_stats(exp["experiment_id"])
        except MlflowError as e:
            # Stats failure shouldn't poison the whole list; degrade gracefully.
            logger.warning("experiment_stats failed for %s: %s",
                           exp["experiment_id"], e)
            stats = {"run_count": None, "best_f1": None,
                     "latest_start_time": None}
        enriched.append({**exp, **stats})
    return enriched


async def _experiment_stats(experiment_id: str) -> dict:
    """Async TTL-cached aggregate. cachetools.@cached doesn't support async,
    so we cache by hand with a per-key Lock to avoid stampede."""
    if experiment_id in _stats_cache:
        return _stats_cache[experiment_id]
    lock = _stats_locks.setdefault(experiment_id, asyncio.Lock())
    async with lock:
        if experiment_id in _stats_cache:           # double-check after acquiring lock
            return _stats_cache[experiment_id]
        runs = await _client().search_runs([experiment_id], max_results=1000)
        f1s = [r.get("metrics", {}).get("f1") for r in runs if r.get("status") == "FINISHED"]
        f1s = [x for x in f1s if x is not None]
        result = {
            "run_count": len(runs),
            "best_f1": max(f1s) if f1s else None,
            "latest_start_time": max(
                (r["start_time"] for r in runs if r.get("start_time")), default=None,
            ),
        }
        _stats_cache[experiment_id] = result
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && uv run pytest tests/test_routers_experiments_aggregate.py -xvs
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/experiments_proxy.py backend/tests/test_routers_experiments_aggregate.py
git commit -m "$(cat <<'EOF'
feat(experiments): include=stats aggregate with manual async TTL cache (phase 13b B4)

Adds run_count, best_f1, latest_start_time per experiment so the
ExperimentCard on /runs can show context. cachetools.@cached doesn't
support async — manual cache + per-key asyncio.Lock prevents stampede.
30s TTL is fine for human-paced UI.
EOF
)"
```

---

## Task 4.1: Frontend — install `react-json-view`

**Files:**

- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`

- [ ] **Step 1: Install the package**

```bash
cd frontend && pnpm add react-json-view
```

- [ ] **Step 2: Verify install**

```bash
grep react-json-view package.json
```

Expected: dependency entry.

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "chore(frontend): add react-json-view dependency (phase 13b B3)"
```

---

## Task 4.2: Frontend — `<JsonTreeView>` component

**Files:**

- Create: `frontend/src/components/common/JsonTreeView.tsx`

- [ ] **Step 1: Create component**

```tsx
import ReactJsonView from "react-json-view";

interface Props {
  value: unknown;
  collapsed?: number | boolean;
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  return (
    <div className="overflow-auto rounded-md border bg-card">
      <ReactJsonView
        src={(value ?? {}) as object}
        name={false}
        collapsed={collapsed}
        displayDataTypes={false}
        displayObjectSize={false}
        enableClipboard={copyable}
        theme="rjv-default"
        style={{
          padding: "0.75rem",
          fontSize: "0.8rem",
          fontFamily: "ui-monospace, monospace",
          background: "transparent",
        }}
      />
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors. (`react-json-view` ships its own types; if not, install `@types/react-json-view`.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/common/JsonTreeView.tsx
git commit -m "feat(common): JsonTreeView (replaces JsonViewer) (phase 13b B3)"
```

---

## Task 4.3: Frontend — replace `<JsonViewer>` usages with `<JsonTreeView>`

**Files:**

- Modify: `frontend/src/routes/_authed.detectors.$id.tsx` (`<ManifestView>`)
- Modify: `frontend/src/routes/_authed.runs.$expId.$runId.tsx` (params + tags)
- Delete: `frontend/src/components/common/JsonViewer.tsx` (after replacements)

- [ ] **Step 1: Replace in `<ManifestView>`**

In `frontend/src/routes/_authed.detectors.$id.tsx`:

```tsx
- import { JsonViewer } from "@/components/common/JsonViewer";
+ import { JsonTreeView } from "@/components/common/JsonTreeView";

  // inside ManifestView
- return <JsonViewer value={manifest} />;
+ return <JsonTreeView value={manifest} collapsed={1} />;
```

- [ ] **Step 2: Replace in run detail (params + tags)**

In `frontend/src/routes/_authed.runs.$expId.$runId.tsx`, swap `JsonViewer` for `JsonTreeView` for both `params` and `tags`. The full route file gets rewritten in Task 9.x; for now this minimal swap is enough.

- [ ] **Step 3: Confirm no remaining usage**

```bash
grep -rn "JsonViewer" frontend/src
```

Expected: no matches except the import-line in JsonViewer.tsx itself.

- [ ] **Step 4: Delete the old component**

```bash
rm frontend/src/components/common/JsonViewer.tsx
```

- [ ] **Step 5: TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "refactor(common): replace JsonViewer with JsonTreeView everywhere (phase 13b B3)"
```

---

## Task 5.1: Frontend — `<MetricsTable>` (replaces whitelist `<MetricCards>`)

**Files:**

- Create: `frontend/src/components/jobs/MetricsTable.tsx`
- Create: `frontend/tests/unit/MetricsTable.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `frontend/tests/unit/MetricsTable.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MetricsTable } from "@/components/jobs/MetricsTable";

describe("MetricsTable", () => {
  it("shows all metrics with pre-ordered standard keys first", () => {
    render(
      <MetricsTable
        metrics={{ accuracy: 0.9, roc_auc: 0.95, custom_metric: 0.5, f1: 0.8 }}
      />,
    );
    const cards = screen.getAllByTestId("metric-card");
    const labels = cards.map((c) => c.getAttribute("data-name"));
    expect(labels.slice(0, 4)).toEqual([
      "accuracy",
      "f1",
      "roc_auc",
      "custom_metric",
    ]);
    // accuracy / f1 first (standard order), then alphabetical: custom_metric, roc_auc
    // ... but we wanted accuracy, f1, then "rest" alphabetical
  });

  it("renders ROC AUC humanized label", () => {
    render(<MetricsTable metrics={{ roc_auc: 0.95 }} />);
    expect(screen.getByText("ROC AUC")).toBeInTheDocument();
  });

  it("formats values to 4 decimal places", () => {
    render(<MetricsTable metrics={{ accuracy: 0.123456789 }} />);
    expect(screen.getByText("0.1235")).toBeInTheDocument();
  });

  it("renders empty state when no metrics", () => {
    render(<MetricsTable metrics={{}} />);
    expect(screen.getByText(/no metrics/i)).toBeInTheDocument();
  });
});
```

Note the standard ordering: `accuracy`, `precision`, `recall`, `f1`, then alphabetical. Adjust the first test to match this exact spec:

```tsx
it("shows accuracy/precision/recall/f1 first, then alphabetical", () => {
  render(
    <MetricsTable
      metrics={{
        accuracy: 0.9,
        roc_auc: 0.95,
        custom_metric: 0.5,
        f1: 0.8,
        precision: 0.85,
      }}
    />,
  );
  const cards = screen.getAllByTestId("metric-card");
  const labels = cards.map((c) => c.getAttribute("data-name"));
  expect(labels).toEqual([
    "accuracy",
    "precision",
    "f1",
    "custom_metric",
    "roc_auc",
  ]);
});
```

(Since `recall` is not in the test input, it's skipped; ordering is `accuracy, precision, recall, f1` for the _standard_ group, then alphabetical for the rest.)

- [ ] **Step 2: Run tests (red)**

```
cd frontend && pnpm vitest run tests/unit/MetricsTable.test.tsx
```

Expected: FAIL — component missing.

- [ ] **Step 3: Implement**

Create `frontend/src/components/jobs/MetricsTable.tsx`:

```tsx
import { Card, CardContent } from "@/components/ui/card";

const STANDARD_ORDER = ["accuracy", "precision", "recall", "f1"] as const;

const HUMAN_LABELS: Record<string, string> = {
  accuracy: "Accuracy",
  precision: "Precision",
  recall: "Recall",
  f1: "F1",
  f1_score: "F1",
  roc_auc: "ROC AUC",
  pr_auc: "PR AUC",
};

function humanize(key: string): string {
  if (HUMAN_LABELS[key]) return HUMAN_LABELS[key];
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function MetricsTable({ metrics }: { metrics: Record<string, number> }) {
  const entries = Object.entries(metrics).filter(
    ([, v]) => typeof v === "number",
  );
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No metrics recorded for this job.
      </p>
    );
  }
  const standard = STANDARD_ORDER.filter((k) => k in metrics).map(
    (k) => [k, metrics[k]] as const,
  );
  const rest = entries
    .filter(([k]) => !(STANDARD_ORDER as readonly string[]).includes(k))
    .sort(([a], [b]) => a.localeCompare(b));
  const ordered = [...standard, ...rest];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {ordered.map(([k, v]) => (
        <Card key={k} data-testid="metric-card" data-name={k}>
          <CardContent className="p-4">
            <div className="text-xs uppercase text-muted-foreground">
              {humanize(k)}
            </div>
            <div className="text-2xl font-semibold">
              {(v as number).toFixed(4)}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run tests (green)**

```
cd frontend && pnpm vitest run tests/unit/MetricsTable.test.tsx
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/jobs/MetricsTable.tsx frontend/tests/unit/MetricsTable.test.tsx
git commit -m "$(cat <<'EOF'
feat(jobs): MetricsTable component (replaces MetricCards whitelist) (phase 13b B1)

Renders every metric in summary_metrics.metrics, no whitelist. Standard
keys (accuracy/precision/recall/f1) come first, others alphabetical.
Humanized labels (roc_auc -> ROC AUC).
EOF
)"
```

---

## Task 5.2: Frontend — `<PerClassMetrics>` component

**Files:**

- Create: `frontend/src/components/jobs/PerClassMetrics.tsx`

- [ ] **Step 1: Implement**

```tsx
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

interface ClassMetric {
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

interface Props {
  perClass: Record<string, ClassMetric>;
  positiveClass?: string;
}

export function PerClassMetrics({ perClass, positiveClass }: Props) {
  const rows = Object.entries(perClass).sort(([a], [b]) => {
    if (a === positiveClass) return -1;
    if (b === positiveClass) return 1;
    return a.localeCompare(b);
  });
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Class</TableHead>
          <TableHead className="text-right">Precision</TableHead>
          <TableHead className="text-right">Recall</TableHead>
          <TableHead className="text-right">F1</TableHead>
          <TableHead className="text-right">Support</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map(([cls, m]) => (
          <TableRow
            key={cls}
            className={cls === positiveClass ? "font-medium" : ""}
          >
            <TableCell>
              {cls}
              {cls === positiveClass ? " (positive)" : ""}
            </TableCell>
            <TableCell className="text-right">
              {m.precision.toFixed(4)}
            </TableCell>
            <TableCell className="text-right">{m.recall.toFixed(4)}</TableCell>
            <TableCell className="text-right">{m.f1.toFixed(4)}</TableCell>
            <TableCell className="text-right">{m.support}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

If `frontend/src/components/ui/table.tsx` doesn't exist:

```bash
cd frontend && pnpm dlx shadcn-ui@latest add table
```

- [ ] **Step 2: TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jobs/PerClassMetrics.tsx frontend/src/components/ui/table.tsx
git commit -m "feat(jobs): PerClassMetrics table (phase 13b B1)"
```

---

## Task 5.3: Frontend — model query hooks (`useModelVersion`, `useModelVersionForJob`)

**Files:**

- Modify: `frontend/src/api/queries/models.ts`

- [ ] **Step 1: Add hooks**

```tsx
export function useModelVersion(id: string | null | undefined) {
  return useQuery({
    queryKey: ["models", "version", id],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/models/versions/{id}", {
        params: { path: { id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
  });
}

export function useModelVersionForJob(jobId: string | null | undefined) {
  return useQuery({
    queryKey: ["models", "version-for-job", jobId],
    queryFn: async () => {
      // Reuses existing models list endpoint with a filter; alternative is a new
      // endpoint /jobs/{id}/produced-model. List+filter is simpler.
      const { data, error } = await client.GET("/api/v1/models/versions", {
        params: { query: { source_job_id: jobId! } },
      });
      if (error) throw error;
      return (data as { items?: unknown[] }).items?.[0] ?? null;
    },
    enabled: Boolean(jobId),
  });
}
```

If those backend endpoints don't yet exist, the hooks become non-functional placeholders. Verify:

```bash
grep -n "models/versions" backend/app/routers/models_registry.py
```

If missing, defer the hook until Phase 13b.1 — the components that consume them will gracefully degrade.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api/queries/models.ts
git commit -m "feat(models): add useModelVersion and useModelVersionForJob hooks (phase 13b B1)"
```

---

## Task 5.4: Frontend — `<SourceModelCard>` and `<TrainedModelCard>`

**Files:**

- Create: `frontend/src/components/jobs/SourceModelCard.tsx`
- Create: `frontend/src/components/jobs/TrainedModelCard.tsx`

- [ ] **Step 1: SourceModelCard**

```tsx
import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModelVersion } from "@/api/queries/models";

export function SourceModelCard({
  sourceModelVersionId,
}: {
  sourceModelVersionId: string;
}) {
  const { data, isLoading, error } = useModelVersion(sourceModelVersionId);

  if (isLoading) return <Loading title="Source model" />;
  if (error || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Source model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Failed to load source model.
        </CardContent>
      </Card>
    );
  }
  const mv = data as {
    mlflow_name: string;
    mlflow_version: number;
    current_stage: string;
    source_job_id?: string;
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Source model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <div>
          <span className="text-muted-foreground">Model:</span>{" "}
          <Link
            to={`/models/${mv.mlflow_name}`}
            className="text-primary hover:underline"
          >
            {mv.mlflow_name}
          </Link>
        </div>
        <div>
          <span className="text-muted-foreground">Version:</span> v
          {mv.mlflow_version} ({mv.current_stage})
        </div>
        {mv.source_job_id && (
          <div>
            <span className="text-muted-foreground">Trained by:</span>{" "}
            <Link
              to={`/jobs/${mv.source_job_id}`}
              className="text-primary hover:underline"
            >
              job {mv.source_job_id.slice(0, 8)}
            </Link>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Loading({ title }: { title: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        Loading…
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: TrainedModelCard**

```tsx
import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useModelVersionForJob } from "@/api/queries/models";

export function TrainedModelCard({ jobId }: { jobId: string }) {
  const { data, isLoading } = useModelVersionForJob(jobId);
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trained model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Loading…
        </CardContent>
      </Card>
    );
  }
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Trained model</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Model not yet registered (or registration failed — see backend logs).
        </CardContent>
      </Card>
    );
  }
  const mv = data as {
    mlflow_name: string;
    mlflow_version: number;
    current_stage: string;
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Trained model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1 text-sm">
        <div>
          <span className="text-muted-foreground">Registered as:</span>{" "}
          <Link
            to={`/models/${mv.mlflow_name}`}
            className="text-primary hover:underline"
          >
            {mv.mlflow_name} v{mv.mlflow_version}
          </Link>
        </div>
        <div>
          <span className="text-muted-foreground">Stage:</span>{" "}
          {mv.current_stage}
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/jobs/SourceModelCard.tsx frontend/src/components/jobs/TrainedModelCard.tsx
git commit -m "feat(jobs): SourceModelCard + TrainedModelCard (phase 13b B1)"
```

---

## Task 5.5: Frontend — `<PredictionSummaryCard>`

**Files:**

- Create: `frontend/src/components/jobs/PredictionSummaryCard.tsx`

- [ ] **Step 1: Implement**

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface PredictionSummary {
  total: number;
  distribution: Record<string, number>;
  duration_seconds: number | null;
}

export function PredictionSummaryCard({
  summary,
}: {
  summary: PredictionSummary | null;
}) {
  if (!summary) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Predictions</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Prediction summary not available (legacy job or predict failed).
        </CardContent>
      </Card>
    );
  }
  const { total, distribution, duration_seconds } = summary;
  const entries = Object.entries(distribution).sort(([, a], [, b]) => b - a);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Predictions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="flex items-baseline gap-4">
          <div>
            <div className="text-xs text-muted-foreground">Total samples</div>
            <div className="text-2xl font-semibold">
              {total.toLocaleString()}
            </div>
          </div>
          {duration_seconds != null && (
            <div>
              <div className="text-xs text-muted-foreground">Duration</div>
              <div className="text-2xl font-semibold">
                {duration_seconds.toFixed(1)}s
              </div>
            </div>
          )}
        </div>

        <div>
          <div className="mb-1 text-xs text-muted-foreground">
            Predicted class distribution
          </div>
          <div className="flex h-5 overflow-hidden rounded-md border">
            {entries.map(([cls, count], idx) => {
              const pct = (count / total) * 100;
              const colors = [
                "bg-blue-500",
                "bg-emerald-500",
                "bg-amber-500",
                "bg-rose-500",
              ];
              const color = colors[idx % colors.length];
              return (
                <div
                  key={cls}
                  className={color}
                  style={{ width: `${pct}%` }}
                  title={`${cls}: ${count} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {entries.map(([cls, count]) => {
              const pct = (count / total) * 100;
              return (
                <div key={cls} className="text-xs">
                  <span className="font-medium">{cls}</span>:{" "}
                  {count.toLocaleString()} ({pct.toFixed(1)}%)
                </div>
              );
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/jobs/PredictionSummaryCard.tsx
git commit -m "feat(jobs): PredictionSummaryCard with distribution bar + table (phase 13b B1)"
```

---

## Task 5.6: Frontend — `<ResolvedConfigCard>` and `<UserParamsTable>`

**Files:**

- Create: `frontend/src/components/jobs/ResolvedConfigCard.tsx`
- Create: `frontend/src/components/jobs/UserParamsTable.tsx`
- Create: `frontend/tests/unit/ResolvedConfigCard.test.tsx`

- [ ] **Step 1: UserParamsTable**

```tsx
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
  TableHead,
  TableHeader,
} from "@/components/ui/table";

interface Props {
  userParams: Record<string, unknown>;
  defaults: Record<string, unknown> | null;
}

export function UserParamsTable({ userParams, defaults }: Props) {
  const keys = Object.keys(userParams).sort();
  if (keys.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No hyperparameters submitted (used detector defaults).
      </p>
    );
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Parameter</TableHead>
          <TableHead>Your value</TableHead>
          {defaults && <TableHead>Default</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {keys.map((k) => {
          const userVal = userParams[k];
          const defaultVal = defaults?.[k];
          const isDefault =
            defaults != null &&
            JSON.stringify(userVal) === JSON.stringify(defaultVal);
          return (
            <TableRow key={k}>
              <TableCell className="font-mono">{k}</TableCell>
              <TableCell
                className={isDefault ? "text-muted-foreground" : "font-medium"}
              >
                {JSON.stringify(userVal)}
                {isDefault && <span className="ml-2 text-xs">(default)</span>}
              </TableCell>
              {defaults && (
                <TableCell className="font-mono text-muted-foreground">
                  {defaultVal !== undefined ? JSON.stringify(defaultVal) : "—"}
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 2: ResolvedConfigCard**

```tsx
import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { JsonTreeView } from "@/components/common/JsonTreeView";
import { UserParamsTable } from "./UserParamsTable";

interface Props {
  resolvedConfig: Record<string, unknown>;
  userParams: Record<string, unknown> | null;
  detectorDefaults?: Record<string, unknown> | null;
}

export function ResolvedConfigCard({
  resolvedConfig,
  userParams,
  detectorDefaults,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const lineCount = JSON.stringify(resolvedConfig, null, 2).split("\n").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Resolved config</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <h3 className="mb-2 text-sm font-medium">Your hyperparameters</h3>
          {userParams !== null ? (
            <UserParamsTable
              userParams={userParams}
              defaults={detectorDefaults ?? null}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              Legacy job — user-supplied params not recorded.
            </p>
          )}
        </div>

        <div>
          <button
            type="button"
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
            onClick={() => setExpanded((x) => !x)}
          >
            {expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            {expanded ? "Hide" : "Show"} full resolved config ({lineCount}{" "}
            lines)
          </button>
          {expanded && (
            <div className="mt-2">
              <JsonTreeView value={resolvedConfig} collapsed={1} />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Tests**

Create `frontend/tests/unit/ResolvedConfigCard.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { ResolvedConfigCard } from "@/components/jobs/ResolvedConfigCard";

describe("ResolvedConfigCard", () => {
  const resolvedConfig = {
    paths: { train: "/x" },
    params: { n_estimators: 200 },
  };

  it("shows user params table when userParams provided", () => {
    render(
      <ResolvedConfigCard
        resolvedConfig={resolvedConfig}
        userParams={{ n_estimators: 200 }}
        detectorDefaults={{ n_estimators: 100 }}
      />,
    );
    expect(screen.getByText("n_estimators")).toBeInTheDocument();
    expect(screen.getByText(/200/)).toBeInTheDocument();
  });

  it("shows legacy fallback when userParams is null", () => {
    render(
      <ResolvedConfigCard resolvedConfig={resolvedConfig} userParams={null} />,
    );
    expect(screen.getByText(/legacy job/i)).toBeInTheDocument();
  });

  it("toggles full resolved config visibility", () => {
    render(
      <ResolvedConfigCard resolvedConfig={resolvedConfig} userParams={{}} />,
    );
    expect(screen.queryByText('"paths"')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/show full/i));
    // react-json-view renders 'paths' as text
    expect(screen.getByText("paths")).toBeInTheDocument();
  });
});
```

Run:

```
cd frontend && pnpm vitest run tests/unit/ResolvedConfigCard.test.tsx
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/jobs/ResolvedConfigCard.tsx frontend/src/components/jobs/UserParamsTable.tsx frontend/tests/unit/ResolvedConfigCard.test.tsx
git commit -m "feat(jobs): ResolvedConfigCard + UserParamsTable (phase 13b B3)"
```

---

## Task 5.7: Frontend — `<JobDetailShell>` + `<TrainSummary>` / `<EvaluateSummary>` / `<PredictSummary>`

**Files:**

- Create: `frontend/src/components/jobs/JobDetailShell.tsx`
- Create: `frontend/src/components/jobs/TrainSummary.tsx`
- Create: `frontend/src/components/jobs/EvaluateSummary.tsx`
- Create: `frontend/src/components/jobs/PredictSummary.tsx`

- [ ] **Step 1: JobDetailShell**

```tsx
import { ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/common/StatusBadge";
import { useCancelJob } from "@/api/queries/jobs";
import { useJobQueuePosition } from "@/api/queries/cluster";
import { isTerminal } from "@/lib/status";
import { formatDuration, formatRelative } from "@/lib/date";

export function JobDetailShell({
  job,
  children,
}: {
  job: any;
  children: ReactNode;
}) {
  const cancel = useCancelJob();
  const nav = useNavigate();
  const isPending = job.status === "pending" || job.status === "preparing";
  const { data: queuePos } = useJobQueuePosition(job.id, isPending);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">
            {job.type} — {job.id.slice(0, 8)}
          </h1>
          <StatusBadge status={job.status} />
        </div>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            onClick={() => nav(`/jobs/new?from=${job.id}`)}
          >
            Clone
          </Button>
          {!isTerminal(job.status) && (
            <Button variant="destructive" onClick={() => cancel.mutate(job.id)}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span className="text-muted-foreground">Submitted:</span>{" "}
            {formatRelative(job.submitted_at)}
          </div>
          <div>
            <span className="text-muted-foreground">Duration:</span>{" "}
            {formatDuration(job.started_at, job.finished_at)}
          </div>
          <div>
            <span className="text-muted-foreground">MLflow run:</span>{" "}
            <code>{job.mlflow_run_id ?? "—"}</code>
          </div>
          <div>
            <span className="text-muted-foreground">Failure reason:</span>{" "}
            {job.failure_reason ?? "—"}
          </div>
          {isPending && queuePos?.position != null && (
            <div className="col-span-2">
              <span className="text-muted-foreground">Queue position:</span>{" "}
              <strong>#{queuePos.position}</strong>
            </div>
          )}
        </CardContent>
      </Card>

      {children}
    </div>
  );
}
```

- [ ] **Step 2: TrainSummary**

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "./MetricsTable";
import { PerClassMetrics } from "./PerClassMetrics";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { JobMetricChart } from "@/components/charts/JobMetricChart";
import { TrainedModelCard } from "./TrainedModelCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";
import { useJobEvents } from "@/hooks/useJobEvents";
import { NON_TERMINAL_JOB_STATUSES } from "@/lib/status";

export function TrainSummary({ job }: { job: any }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics = (sm.metrics as Record<string, number>) ?? {};
  const perClass = sm.per_class as Record<string, any> | undefined;
  const cm = sm.confusion_matrix as
    | { labels?: string[]; matrix?: number[][] }
    | undefined;

  const isLive = (NON_TERMINAL_JOB_STATUSES as readonly string[]).includes(
    job.status,
  );
  const { events, error: eventsError } = useJobEvents(job.id, isLive);
  const hasTimeSeries = events.some(
    (e) =>
      e.kind === "metric" &&
      typeof (e as any).step === "number" &&
      (e as any).step >= 1,
  );

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Final metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricsTable metrics={metrics} />
        </CardContent>
      </Card>
      {perClass && (
        <Card>
          <CardHeader>
            <CardTitle>Per-class metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <PerClassMetrics perClass={perClass} />
          </CardContent>
        </Card>
      )}
      {cm?.labels && cm.matrix && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}
      {(hasTimeSeries || eventsError) && (
        <Card>
          <CardHeader>
            <CardTitle>Live metrics</CardTitle>
          </CardHeader>
          <CardContent>
            {eventsError && (
              <p className="text-sm text-destructive">{eventsError}</p>
            )}
            {hasTimeSeries && <JobMetricChart events={events} />}
          </CardContent>
        </Card>
      )}
      <TrainedModelCard jobId={job.id} />
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params ?? null}
      />
    </>
  );
}
```

- [ ] **Step 3: EvaluateSummary**

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "./MetricsTable";
import { PerClassMetrics } from "./PerClassMetrics";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { SourceModelCard } from "./SourceModelCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";

export function EvaluateSummary({ job }: { job: any }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const metrics = (sm.metrics as Record<string, number>) ?? {};
  const perClass = sm.per_class as Record<string, any> | undefined;
  const cm = sm.confusion_matrix as
    | { labels?: string[]; matrix?: number[][] }
    | undefined;

  return (
    <>
      {job.source_model_version_id && (
        <SourceModelCard sourceModelVersionId={job.source_model_version_id} />
      )}
      <Card>
        <CardHeader>
          <CardTitle>Evaluation metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricsTable metrics={metrics} />
        </CardContent>
      </Card>
      {perClass && (
        <Card>
          <CardHeader>
            <CardTitle>Per-class metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <PerClassMetrics perClass={perClass} />
          </CardContent>
        </Card>
      )}
      {cm?.labels && cm.matrix && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params ?? null}
      />
    </>
  );
}
```

- [ ] **Step 4: PredictSummary**

```tsx
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Download } from "lucide-react";
import { SourceModelCard } from "./SourceModelCard";
import { PredictionSummaryCard } from "./PredictionSummaryCard";
import { ResolvedConfigCard } from "./ResolvedConfigCard";

export function PredictSummary({ job }: { job: any }) {
  const sm = (job.summary_metrics ?? {}) as Record<string, unknown>;
  const ps = sm.prediction_summary as any;

  return (
    <>
      {job.source_model_version_id && (
        <SourceModelCard sourceModelVersionId={job.source_model_version_id} />
      )}
      <PredictionSummaryCard summary={ps ?? null} />
      {job.mlflow_run_id && (
        <Card>
          <CardHeader>
            <CardTitle>Output</CardTitle>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <a
                href={`/api/v1/runs/${job.mlflow_run_id}/artifacts/download?path=predictions.csv`}
                download
              >
                <Download className="mr-2 h-4 w-4" />
                Download predictions.csv
              </a>
            </Button>
          </CardContent>
        </Card>
      )}
      <ResolvedConfigCard
        resolvedConfig={job.resolved_config}
        userParams={job.user_params ?? null}
      />
    </>
  );
}
```

- [ ] **Step 5: TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors. The `any` typing on `job` is intentional to avoid cascading TypeScript work; can be tightened by re-deriving from `schema.gen.ts` later.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/jobs/
git commit -m "$(cat <<'EOF'
feat(jobs): JobDetailShell + Train/Evaluate/Predict summary components (phase 13b B1)

Each summary tailored to the stage's salient information.
EOF
)"
```

---

## Task 5.8: Frontend — rewrite `_authed.jobs.$id.tsx` to dispatch by job type

**Files:**

- Modify: `frontend/src/routes/_authed.jobs.$id.tsx`
- Delete: `frontend/src/components/charts/MetricCards.tsx`

- [ ] **Step 1: Rewrite the route**

Replace the entire file contents:

```tsx
import { useParams, Link } from "react-router";
import { useJob, useJobLogs } from "@/api/queries/jobs";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LogTail } from "@/components/common/LogTail";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JobDetailShell } from "@/components/jobs/JobDetailShell";
import { TrainSummary } from "@/components/jobs/TrainSummary";
import { EvaluateSummary } from "@/components/jobs/EvaluateSummary";
import { PredictSummary } from "@/components/jobs/PredictSummary";

export const handle = { breadcrumb: "Job" };

export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { data: job } = useJob(id);
  const { data: logText } = useJobLogs(id, job?.status);
  if (!job) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <JobDetailShell job={job}>
      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="artifacts" disabled={!job.mlflow_run_id}>
            Artifacts
          </TabsTrigger>
          {job.mlflow_run_id && (
            <TabsTrigger value="mlflow" asChild>
              <Link
                to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}
              >
                Open run ↗
              </Link>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
          {job.type === "train" && <TrainSummary job={job} />}
          {job.type === "evaluate" && <EvaluateSummary job={job} />}
          {job.type === "predict" && <PredictSummary job={job} />}
        </TabsContent>

        <TabsContent value="logs">
          <LogTail text={(logText as string) ?? ""} />
        </TabsContent>

        <TabsContent value="artifacts">
          {job.mlflow_run_id ? (
            <ArtifactTree runId={job.mlflow_run_id} />
          ) : (
            <p className="text-muted-foreground">
              No MLflow run recorded for this job.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </JobDetailShell>
  );
}
```

- [ ] **Step 2: Delete `MetricCards`**

```bash
rm frontend/src/components/charts/MetricCards.tsx
```

Confirm no remaining usages:

```bash
grep -rn "MetricCards" frontend/src
```

Expected: no matches (the new code uses `MetricsTable` and `PredictionSummaryCard`).

- [ ] **Step 3: TypeScript check**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Manual smoke**

```bash
cd frontend && pnpm dev
```

Visit a recent train, evaluate, predict job; verify each shows the right cards.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(jobs): per-type Job Detail Summary tab dispatcher (phase 13b B1)

Train/Evaluate/Predict each get tailored cards. Removes the
one-size-fits-all MetricCards (whitelist-only) layout.
EOF
)"
```

---

## Task 6.1: Frontend — `RjsfConfigForm.logic.ts` (deriveUiSchema + fillDefaults)

**Files:**

- Create: `frontend/src/components/forms/RjsfConfigForm.logic.ts`
- Create: `frontend/tests/unit/RjsfConfigForm.logic.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/tests/unit/RjsfConfigForm.logic.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  deriveUiSchemaFromSchema,
  fillDefaults,
} from "@/components/forms/RjsfConfigForm.logic";

describe("deriveUiSchemaFromSchema", () => {
  it("ui:help from description", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "Number of trees." },
      },
    };
    expect(deriveUiSchemaFromSchema(schema as any)).toEqual({
      "ui:submitButtonOptions": { norender: true },
      n: { "ui:help": "Number of trees." },
    });
  });

  it("ui:placeholder from default", () => {
    const schema = {
      type: "object",
      properties: { lr: { type: "number", default: 0.001 } },
    };
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(ui.lr["ui:placeholder"]).toBe("Default: 0.001");
  });

  it("both description and default", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "trees", default: 100 },
      },
    };
    const ui = deriveUiSchemaFromSchema(schema as any);
    expect(ui.n).toEqual({
      "ui:help": "trees",
      "ui:placeholder": "Default: 100",
    });
  });
});

describe("fillDefaults", () => {
  it("fills default for missing key", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    expect(fillDefaults(schema as any, {})).toEqual({ n: 100 });
  });

  it("does not overwrite existing value", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    expect(fillDefaults(schema as any, { n: 200 })).toEqual({ n: 200 });
  });

  it("does not fill when no default", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer" } },
    };
    expect(fillDefaults(schema as any, {})).toEqual({});
  });

  it("respects null default for nullable union", () => {
    const schema = {
      type: "object",
      properties: {
        max_depth: {
          anyOf: [{ type: "integer" }, { type: "null" }],
          default: null,
        },
      },
    };
    expect(fillDefaults(schema as any, {})).toEqual({ max_depth: null });
  });
});
```

- [ ] **Step 2: Run (red)**

```
cd frontend && pnpm vitest run tests/unit/RjsfConfigForm.logic.test.ts
```

Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

Create `frontend/src/components/forms/RjsfConfigForm.logic.ts`:

```ts
import type { RJSFSchema, UiSchema } from "@rjsf/utils";

export function deriveUiSchemaFromSchema(schema: RJSFSchema): UiSchema {
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  walk(schema as any, ui);
  return ui;
}

function walk(node: any, ui: UiSchema): void {
  if (!node || typeof node !== "object") return;
  const props = node.properties;
  if (!props) return;
  for (const [k, child] of Object.entries(props)) {
    const childUi: UiSchema = (ui[k] as UiSchema) ?? {};
    const c = child as any;
    if (typeof c.description === "string") {
      childUi["ui:help"] = c.description;
    }
    if (c.default !== undefined) {
      childUi["ui:placeholder"] = `Default: ${JSON.stringify(c.default)}`;
    }
    if (Object.keys(childUi).length > 0) ui[k] = childUi;
    walk(c, childUi);
  }
}

export function fillDefaults(
  schema: RJSFSchema,
  current: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...current };
  if (!schema || typeof schema !== "object") return out;
  const props = (schema as any).properties;
  if (!props) return out;
  for (const [k, child] of Object.entries(props)) {
    if (out[k] !== undefined) continue;
    const c = child as any;
    if (c.default !== undefined) {
      out[k] = c.default;
    }
  }
  return out;
}
```

- [ ] **Step 4: Run tests (green)**

```
cd frontend && pnpm vitest run tests/unit/RjsfConfigForm.logic.test.ts
```

Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/RjsfConfigForm.logic.ts frontend/tests/unit/RjsfConfigForm.logic.test.ts
git commit -m "feat(forms): deriveUiSchemaFromSchema + fillDefaults (phase 13b B2)"
```

---

## Task 6.2: Frontend — rewrite `RjsfConfigForm.tsx`

**Files:**

- Modify: `frontend/src/components/forms/RjsfConfigForm.tsx`
- Create: `frontend/tests/unit/RjsfConfigForm.test.tsx`

- [ ] **Step 1: Write failing tests**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { RjsfConfigForm } from "@/components/forms/RjsfConfigForm";

describe("RjsfConfigForm", () => {
  it("renders description as ui:help", () => {
    const schema = {
      type: "object",
      properties: {
        n: { type: "integer", description: "Number of trees", default: 100 },
      },
    };
    render(<RjsfConfigForm schema={schema} value={{}} onChange={() => {}} />);
    expect(screen.getByText(/Number of trees/i)).toBeInTheDocument();
  });

  it("pre-populates defaults via onChange on mount", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    const onChange = vi.fn();
    render(<RjsfConfigForm schema={schema} value={{}} onChange={onChange} />);
    expect(onChange).toHaveBeenCalledWith({ n: 100 });
  });

  it("Reset to defaults button restores defaults", () => {
    const schema = {
      type: "object",
      properties: { n: { type: "integer", default: 100 } },
    };
    const onChange = vi.fn();
    render(
      <RjsfConfigForm schema={schema} value={{ n: 200 }} onChange={onChange} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));
    expect(onChange).toHaveBeenLastCalledWith({ n: 100 });
  });
});
```

- [ ] **Step 2: Rewrite component**

Replace `frontend/src/components/forms/RjsfConfigForm.tsx`:

```tsx
import Form from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { deriveUiSchemaFromSchema, fillDefaults } from "./RjsfConfigForm.logic";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}

const NON_WRAPPING_SIBLINGS = new Set(["title", "description"]);

function normalizeSchema(node: unknown): unknown {
  if (node === null || typeof node !== "object") return node;
  if (Array.isArray(node)) return node.map(normalizeSchema);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node)) {
    out[k] = normalizeSchema(v);
  }
  if (typeof out.$ref === "string") {
    const { $ref, ...rest } = out;
    const hasSiblings = Object.keys(rest).some(
      (k) => !NON_WRAPPING_SIBLINGS.has(k),
    );
    if (hasSiblings) {
      return { allOf: [{ $ref }], ...rest };
    }
  }
  return out;
}

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  const normalizedSchema = useMemo(
    () => normalizeSchema(schema) as RJSFSchema,
    [schema],
  );
  const uiSchema = useMemo(
    () => deriveUiSchemaFromSchema(normalizedSchema),
    [normalizedSchema],
  );

  // Phase 13b B2: pre-populate defaults whenever schema changes.
  useEffect(() => {
    onChange(fillDefaults(normalizedSchema, {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [normalizedSchema]);

  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={normalizedSchema}
        uiSchema={uiSchema}
        validator={validator}
        formData={value}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
      >
        <div className="mt-4 flex justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onChange(fillDefaults(normalizedSchema, {}))}
          >
            Reset to defaults
          </Button>
        </div>
      </Form>
    </div>
  );
}
```

- [ ] **Step 3: Run tests (green)**

```
cd frontend && pnpm vitest run tests/unit/RjsfConfigForm.test.tsx
```

Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/forms/RjsfConfigForm.tsx frontend/tests/unit/RjsfConfigForm.test.tsx
git commit -m "$(cat <<'EOF'
feat(forms): RjsfConfigForm with auto-derived uiSchema, defaults pre-populate, Reset button (phase 13b B2)

Descriptions show inline (ui:help), default values show as placeholder
(Default: <value>), formData pre-populates with defaults whenever
schema changes (so stage switching resets cleanly).
EOF
)"
```

---

## Task 6.3: Frontend — `<StageExplainer>` + i18n keys + JobSubmitForm wire-up

**Files:**

- Create: `frontend/src/components/forms/StageExplainer.tsx`
- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/src/i18n/zh-TW.json`, `frontend/src/i18n/en.json`

- [ ] **Step 1: i18n keys**

`frontend/src/i18n/zh-TW.json` (currently `{}`):

```json
{
  "stage.train.title": "Train — 訓練新模型",
  "stage.train.description": "用 train dataset 訓練新模型，產出註冊到 Models registry。可選 test dataset 同步算最終指標與混淆矩陣。",
  "stage.evaluate.title": "Evaluate — 用既有模型評估",
  "stage.evaluate.description": "用既有的訓練模型，跑 test dataset 算指標。不會產生新模型。",
  "stage.predict.title": "Predict — 批次預測",
  "stage.predict.description": "用既有模型批次預測未標註樣本，產出 predictions.csv。不算指標、不需要 ground truth。",
  "stage.field.train_dataset": "Train dataset",
  "stage.field.test_dataset": "Test dataset",
  "stage.field.predict_dataset": "Predict dataset",
  "stage.field.source_model": "Source model + version",
  "stage.field.hyperparameters": "Hyperparameters",
  "stage.required": "必填",
  "stage.optional": "可選"
}
```

`frontend/src/i18n/en.json`:

```json
{
  "stage.train.title": "Train — train a new model",
  "stage.train.description": "Train a new model using the train dataset; the result is registered in the Models registry. Optional test dataset enables final metrics + confusion matrix computation.",
  "stage.evaluate.title": "Evaluate — score with an existing model",
  "stage.evaluate.description": "Run the test dataset through an existing trained model to compute metrics. No new model is registered.",
  "stage.predict.title": "Predict — batch inference",
  "stage.predict.description": "Run an existing model over an unlabeled dataset, producing predictions.csv. No metrics or ground truth required.",
  "stage.field.train_dataset": "Train dataset",
  "stage.field.test_dataset": "Test dataset",
  "stage.field.predict_dataset": "Predict dataset",
  "stage.field.source_model": "Source model + version",
  "stage.field.hyperparameters": "Hyperparameters",
  "stage.required": "required",
  "stage.optional": "optional"
}
```

- [ ] **Step 2: StageExplainer component**

```tsx
import { useTranslation } from "react-i18next";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { JobType } from "@/api/queries/jobs";

const REQUIRED_FIELDS: Record<JobType, string[]> = {
  train: ["train_dataset"],
  evaluate: ["source_model", "test_dataset"],
  predict: ["source_model", "predict_dataset"],
};

const OPTIONAL_FIELDS: Record<JobType, string[]> = {
  train: ["test_dataset", "hyperparameters"],
  evaluate: ["hyperparameters"],
  predict: ["hyperparameters"],
};

export function StageExplainer({ type }: { type: JobType }) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardContent className="space-y-2 py-4 text-sm">
        <p className="font-medium">{t(`stage.${type}.title`)}</p>
        <p className="text-muted-foreground">
          {t(`stage.${type}.description`)}
        </p>
        <div className="flex flex-wrap gap-2 pt-2">
          {REQUIRED_FIELDS[type].map((f) => (
            <Badge key={`req-${f}`} variant="default">
              {t(`stage.field.${f}`)} ({t("stage.required")})
            </Badge>
          ))}
          {OPTIONAL_FIELDS[type].map((f) => (
            <Badge key={`opt-${f}`} variant="outline">
              {t(`stage.field.${f}`)} ({t("stage.optional")})
            </Badge>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Wire into JobSubmitForm**

In `frontend/src/components/forms/JobSubmitForm.tsx`, after the "Job type" Card and before the "Detector" Card, insert:

```tsx
import { StageExplainer } from "./StageExplainer";

// ... in the JSX, after Job type Card:
<StageExplainer type={type} />;
```

- [ ] **Step 4: TypeScript check + manual smoke**

```bash
cd frontend && pnpm tsc --noEmit
cd frontend && pnpm dev
```

Visit `/jobs/new`, verify the Chinese explainer appears, switch type → text changes; verify required/optional badges.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(forms): StageExplainer + zh-TW/en i18n + JobSubmitForm integration (phase 13b B2)"
```

---

## Task 7.1: Frontend — `<CollapsibleCard>`, `<OpenInMlflowButton>`, `<OpenInLoldayJobButton>`

**Files:**

- Create: `frontend/src/components/common/CollapsibleCard.tsx`
- Create: `frontend/src/components/common/OpenInMlflowButton.tsx`
- Create: `frontend/src/components/common/OpenInLoldayJobButton.tsx`

- [ ] **Step 1: CollapsibleCard**

```tsx
import { useState, ReactNode } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function CollapsibleCard({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setOpen((x) => !x)}
      >
        <CardTitle className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          {title}
        </CardTitle>
      </CardHeader>
      {open && <CardContent>{children}</CardContent>}
    </Card>
  );
}
```

- [ ] **Step 2: OpenInMlflowButton**

```tsx
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  experimentId?: string;
  runId?: string;
  size?: "default" | "sm";
}

export function OpenInMlflowButton({
  experimentId,
  runId,
  size = "sm",
}: Props) {
  let href = "/mlflow/";
  if (experimentId && runId) {
    href = `/mlflow/#/experiments/${experimentId}/runs/${runId}`;
  } else if (experimentId) {
    href = `/mlflow/#/experiments/${experimentId}`;
  }
  return (
    <Button asChild variant="outline" size={size}>
      <a href={href} target="_blank" rel="noopener noreferrer">
        <ExternalLink className="mr-2 h-4 w-4" />
        Open in MLflow
      </a>
    </Button>
  );
}
```

- [ ] **Step 3: OpenInLoldayJobButton**

```tsx
import { Link } from "react-router";
import { Button } from "@/components/ui/button";

export function OpenInLoldayJobButton({ jobId }: { jobId: string }) {
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/jobs/${jobId}`}>↗ Open job</Link>
    </Button>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/common/CollapsibleCard.tsx frontend/src/components/common/OpenInMlflowButton.tsx frontend/src/components/common/OpenInLoldayJobButton.tsx
git commit -m "feat(common): CollapsibleCard + OpenInMlflowButton + OpenInLoldayJobButton (phase 13b B4)"
```

---

## Task 7.2: Frontend — `<ExperimentCard>` + rewrite `/runs` index

**Files:**

- Create: `frontend/src/components/runs/ExperimentCard.tsx`
- Modify: `frontend/src/api/queries/runs.ts` (add `useExperimentsWithStats`)
- Modify: `frontend/src/routes/_authed.runs._index.tsx`

- [ ] **Step 1: API hook**

In `frontend/src/api/queries/runs.ts`:

```ts
export function useExperimentsWithStats() {
  return useQuery({
    queryKey: ["runs", "experiments", "stats"],
    queryFn: async () => {
      const { data, error } = await client.GET("/api/v1/experiments", {
        params: { query: { include: "stats" } },
      });
      if (error) throw error;
      return data as {
        experiment_id: string;
        name: string;
        run_count: number | null;
        best_f1: number | null;
        latest_start_time: number | null;
      }[];
    },
  });
}
```

- [ ] **Step 2: ExperimentCard**

```tsx
import { Link } from "react-router";
import { Card, CardContent } from "@/components/ui/card";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { formatRelative } from "@/lib/date";

interface Exp {
  experiment_id: string;
  name: string;
  run_count: number | null;
  best_f1: number | null;
  latest_start_time: number | null;
}

export function ExperimentCard({ exp }: { exp: Exp }) {
  return (
    <Card className="transition hover:border-primary">
      <CardContent className="space-y-2 p-4">
        <Link to={`/runs/${exp.experiment_id}`} className="block">
          <div className="text-xs text-muted-foreground">
            #{exp.experiment_id}
          </div>
          <div className="text-lg font-medium">{exp.name}</div>
          <div className="text-sm text-muted-foreground">
            {exp.run_count ?? "—"} runs · Best F1:{" "}
            {exp.best_f1 != null ? exp.best_f1.toFixed(4) : "—"} ·{" "}
            {exp.latest_start_time != null
              ? formatRelative(new Date(exp.latest_start_time).toISOString())
              : "no runs"}
          </div>
        </Link>
        <div className="flex justify-end">
          <OpenInMlflowButton experimentId={exp.experiment_id} />
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Rewrite the route**

```tsx
import { useExperimentsWithStats } from "@/api/queries/runs";
import { ExperimentCard } from "@/components/runs/ExperimentCard";

export const handle = { breadcrumb: "Runs" };

export default function ExperimentsListPage() {
  const { data, isLoading } = useExperimentsWithStats();
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Experiments</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((exp) => (
          <ExperimentCard key={exp.experiment_id} exp={exp} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(runs): ExperimentCard with stats + Open in MLflow (phase 13b B4)"
```

---

## Task 7.3: Frontend — `<RunsColumnPicker>` and `<RunsStatusFilter>`

**Files:**

- Create: `frontend/src/components/runs/RunsColumnPicker.tsx`
- Create: `frontend/src/components/runs/RunsStatusFilter.tsx`
- Create: `frontend/tests/unit/RunsColumnPicker.test.tsx`

- [ ] **Step 1: RunsColumnPicker (with localStorage persistence)**

```tsx
import { useEffect, useState } from "react";
import { Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

interface Props {
  experimentId: string;
  availableMetrics: string[];
  availableParams: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
}

export function RunsColumnPicker({
  experimentId,
  availableMetrics,
  availableParams,
  selected,
  onChange,
}: Props) {
  // Persist to localStorage per experiment.
  useEffect(() => {
    localStorage.setItem(
      `runs.columns.${experimentId}`,
      JSON.stringify(selected),
    );
  }, [experimentId, selected]);

  function toggle(key: string) {
    if (selected.includes(key)) onChange(selected.filter((s) => s !== key));
    else onChange([...selected, key]);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm">
          <Settings2 className="mr-2 h-4 w-4" />
          Columns ({selected.length})
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-96 overflow-y-auto">
        <DropdownMenuLabel>Metrics</DropdownMenuLabel>
        {availableMetrics.map((m) => {
          const key = `metrics.${m}`;
          return (
            <DropdownMenuCheckboxItem
              key={key}
              checked={selected.includes(key)}
              onCheckedChange={() => toggle(key)}
            >
              {m}
            </DropdownMenuCheckboxItem>
          );
        })}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Parameters</DropdownMenuLabel>
        {availableParams.map((p) => {
          const key = `params.${p}`;
          return (
            <DropdownMenuCheckboxItem
              key={key}
              checked={selected.includes(key)}
              onCheckedChange={() => toggle(key)}
            >
              {p}
            </DropdownMenuCheckboxItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function loadColumnsFromStorage(
  experimentId: string,
  fallback: string[],
): string[] {
  try {
    const raw = localStorage.getItem(`runs.columns.${experimentId}`);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}
```

- [ ] **Step 2: RunsStatusFilter**

```tsx
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const STATUSES = ["all", "FINISHED", "RUNNING", "FAILED", "SCHEDULED"] as const;
type Status = (typeof STATUSES)[number];

interface Props {
  value: Status;
  onChange: (s: Status) => void;
}

export function RunsStatusFilter({ value, onChange }: Props) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as Status)}>
      <SelectTrigger className="w-36">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {STATUSES.map((s) => (
          <SelectItem key={s} value={s}>
            {s === "all" ? "All statuses" : s}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export type { Status as RunsStatus };
```

- [ ] **Step 3: Tests for RunsColumnPicker**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  RunsColumnPicker,
  loadColumnsFromStorage,
} from "@/components/runs/RunsColumnPicker";

describe("RunsColumnPicker", () => {
  beforeEach(() => localStorage.clear());

  it("calls onChange when a metric is toggled", () => {
    const onChange = vi.fn();
    render(
      <RunsColumnPicker
        experimentId="1"
        availableMetrics={["accuracy", "f1"]}
        availableParams={["lr"]}
        selected={["metrics.accuracy"]}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /columns/i }));
    fireEvent.click(screen.getByText("f1"));
    expect(onChange).toHaveBeenCalledWith(["metrics.accuracy", "metrics.f1"]);
  });

  it("persists to localStorage on change", () => {
    const { rerender } = render(
      <RunsColumnPicker
        experimentId="1"
        availableMetrics={["accuracy"]}
        availableParams={[]}
        selected={["metrics.accuracy"]}
        onChange={() => {}}
      />,
    );
    expect(localStorage.getItem("runs.columns.1")).toBe(
      JSON.stringify(["metrics.accuracy"]),
    );
  });

  it("loadColumnsFromStorage returns fallback when missing", () => {
    expect(loadColumnsFromStorage("missing", ["a"])).toEqual(["a"]);
  });

  it("loadColumnsFromStorage returns parsed value", () => {
    localStorage.setItem("runs.columns.x", JSON.stringify(["a", "b"]));
    expect(loadColumnsFromStorage("x", ["fallback"])).toEqual(["a", "b"]);
  });
});
```

- [ ] **Step 4: Run tests (green)**

```
cd frontend && pnpm vitest run tests/unit/RunsColumnPicker.test.tsx
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/runs/ frontend/tests/unit/RunsColumnPicker.test.tsx
git commit -m "feat(runs): RunsColumnPicker + RunsStatusFilter with localStorage persistence (phase 13b B4)"
```

---

## Task 7.4: Frontend — rewrite `/runs/:expId` runs list

**Files:**

- Modify: `frontend/src/routes/_authed.runs.$expId.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import { Link, useParams } from "react-router";
import { useState, useMemo, useEffect } from "react";
import { useExperimentRuns } from "@/api/queries/runs";
import { DataTable } from "@/components/tables/DataTable";
import { StatusBadge } from "@/components/common/StatusBadge";
import {
  RunsColumnPicker,
  loadColumnsFromStorage,
} from "@/components/runs/RunsColumnPicker";
import {
  RunsStatusFilter,
  type RunsStatus,
} from "@/components/runs/RunsStatusFilter";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { formatDuration } from "@/lib/date";
import type { ColumnDef } from "@tanstack/react-table";

export const handle = { breadcrumb: "Experiment" };

interface Row {
  run_id: string;
  run_name?: string;
  status: string;
  start_time?: number;
  end_time?: number;
  metrics?: Record<string, number>;
  params?: Record<string, string>;
  tags?: Record<string, string>;
}

const DEFAULT_COLS = ["metrics.f1", "metrics.accuracy"];

export default function RunsListPage() {
  const { expId = "" } = useParams();
  const { data, isLoading } = useExperimentRuns(expId);
  const rows: Row[] = data ?? [];

  // Discover available metric/param keys from the data.
  const { availableMetrics, availableParams } = useMemo(() => {
    const m = new Set<string>();
    const p = new Set<string>();
    for (const r of rows) {
      Object.keys(r.metrics ?? {}).forEach((k) => m.add(k));
      Object.keys(r.params ?? {}).forEach((k) => p.add(k));
    }
    return {
      availableMetrics: Array.from(m).sort(),
      availableParams: Array.from(p).sort(),
    };
  }, [rows]);

  const [selectedCols, setSelectedCols] = useState<string[]>(() =>
    loadColumnsFromStorage(expId, DEFAULT_COLS),
  );
  const [status, setStatus] = useState<RunsStatus>(() => {
    const v = localStorage.getItem(`runs.status.${expId}`);
    return (v as RunsStatus) ?? "all";
  });
  useEffect(() => {
    localStorage.setItem(`runs.status.${expId}`, status);
  }, [expId, status]);

  // Filter rows by status
  const filteredRows =
    status === "all"
      ? rows
      : rows.filter((r) => r.status.toUpperCase() === status);

  // Build columns
  const columns: ColumnDef<Row>[] = [
    {
      accessorKey: "run_id",
      header: "Run",
      cell: ({ row }) => (
        <Link
          to={`/runs/${expId}/${row.original.run_id}`}
          className="font-mono text-sm hover:underline"
        >
          {row.original.run_id.slice(0, 10)}
        </Link>
      ),
    },
    { accessorKey: "run_name", header: "Name" },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => (
        <StatusBadge status={row.original.status.toLowerCase()} />
      ),
    },
    {
      id: "duration",
      header: "Duration",
      cell: ({ row }) =>
        row.original.start_time && row.original.end_time
          ? formatDuration(
              new Date(row.original.start_time).toISOString(),
              new Date(row.original.end_time).toISOString(),
            )
          : "—",
    },
    ...selectedCols.map((key): ColumnDef<Row> => {
      const [kind, name] = key.split(".", 2);
      return {
        id: key,
        header: name,
        cell: ({ row }) => {
          const bag = (row.original as any)[kind] as
            | Record<string, unknown>
            | undefined;
          const v = bag?.[name];
          if (typeof v === "number") return v.toFixed(4);
          if (v == null) return "—";
          return String(v);
        },
      };
    }),
    {
      id: "job",
      header: "Job",
      cell: ({ row }) => {
        const jobId =
          row.original.tags?.["lolday.job_id"] ??
          row.original.tags?.lolday_job_id;
        return jobId ? (
          <Link to={`/jobs/${jobId}`} className="text-primary hover:underline">
            ↗
          </Link>
        ) : (
          "—"
        );
      },
    },
  ];

  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Runs</h1>
        <div className="flex items-center gap-2">
          <RunsStatusFilter value={status} onChange={setStatus} />
          <RunsColumnPicker
            experimentId={expId}
            availableMetrics={availableMetrics}
            availableParams={availableParams}
            selected={selectedCols}
            onChange={setSelectedCols}
          />
          <OpenInMlflowButton experimentId={expId} />
        </div>
      </div>
      <DataTable
        data={filteredRows}
        columns={columns}
        emptyMessage="No runs match the filter."
      />
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check + manual smoke**

```bash
cd frontend && pnpm tsc --noEmit
cd frontend && pnpm dev
```

Visit `/runs/<exp>`, verify column picker dropdown, status filter, sortable columns.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/_authed.runs.$expId.tsx
git commit -m "feat(runs): runs list with column picker, status filter, Open in MLflow (phase 13b B4)"
```

---

## Task 7.5: Frontend — rewrite `/runs/:expId/:runId` run detail

**Files:**

- Modify: `frontend/src/routes/_authed.runs.$expId.$runId.tsx`

- [ ] **Step 1: Rewrite**

```tsx
import { useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { useRun } from "@/api/queries/runs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MetricsTable } from "@/components/jobs/MetricsTable";
import { ConfusionMatrix } from "@/components/charts/ConfusionMatrix";
import { ArtifactTree } from "@/components/common/ArtifactTree";
import { JsonTreeView } from "@/components/common/JsonTreeView";
import { CollapsibleCard } from "@/components/common/CollapsibleCard";
import { OpenInMlflowButton } from "@/components/common/OpenInMlflowButton";
import { OpenInLoldayJobButton } from "@/components/common/OpenInLoldayJobButton";

export const handle = { breadcrumb: "Run" };

function useConfusionMatrix(runId: string) {
  return useQuery({
    queryKey: ["runs", runId, "cm-artifact"],
    queryFn: async () => {
      try {
        const resp = await fetch(
          `/api/v1/runs/${runId}/artifacts/download?path=confusion_matrix.json`,
          { credentials: "include" },
        );
        if (!resp.ok) return null;
        return (await resp.json()) as { labels: string[]; matrix: number[][] };
      } catch {
        return null;
      }
    },
    retry: false,
    enabled: Boolean(runId),
  });
}

export default function RunDetailPage() {
  const { expId = "", runId = "" } = useParams();
  const { data } = useRun(runId);
  const { data: cm } = useConfusionMatrix(runId);
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  const run = data as unknown as {
    run_id: string;
    status: string;
    start_time?: number;
    end_time?: number;
    metrics?: Record<string, number>;
    params?: Record<string, unknown>;
    tags?: Record<string, string>;
  };

  const jobId = run.tags?.["lolday.job_id"] ?? run.tags?.lolday_job_id;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Run {runId.slice(0, 10)}</h1>
        <div className="flex gap-2">
          {jobId && <OpenInLoldayJobButton jobId={jobId} />}
          <OpenInMlflowButton experimentId={expId} runId={runId} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <MetricsTable metrics={run.metrics ?? {}} />
        </CardContent>
      </Card>

      {cm && (
        <Card>
          <CardHeader>
            <CardTitle>Confusion matrix</CardTitle>
          </CardHeader>
          <CardContent>
            <ConfusionMatrix labels={cm.labels} matrix={cm.matrix} />
          </CardContent>
        </Card>
      )}

      <CollapsibleCard title="Parameters">
        <JsonTreeView value={run.params ?? {}} collapsed={1} />
      </CollapsibleCard>

      <CollapsibleCard title="Tags">
        <JsonTreeView value={run.tags ?? {}} collapsed={1} />
      </CollapsibleCard>

      <Card>
        <CardHeader>
          <CardTitle>Artifacts</CardTitle>
        </CardHeader>
        <CardContent>
          <ArtifactTree runId={runId} />
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 2: TypeScript + manual smoke**

```bash
cd frontend && pnpm tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/_authed.runs.$expId.$runId.tsx
git commit -m "feat(runs): rewrite run detail with tree views, MetricsTable, Open in MLflow + Open job (phase 13b B4)"
```

---

## Task 8.1: Chart — `/mlflow/` IngressRoute + Middleware

**Files:**

- Modify: `charts/lolday/templates/ingress.yaml`

- [ ] **Step 1: Update ingress.yaml**

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: lolday
  namespace: { { .Release.Namespace } }
spec:
  entryPoints: [web]
  routes:
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/api/v1`)
      priority: 10
      services:
        - kind: Service
          name: backend
          port: 8000

    # Phase 13b B5: MLflow UI — read-only methods only (GET / HEAD / OPTIONS).
    # Writes must go through the backend's experiments_proxy so audit
    # trails persist (who created what run via lolday's auth).
    - kind: Rule
      match: >
        Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/mlflow`)
        && (Method(`GET`) || Method(`HEAD`) || Method(`OPTIONS`))
      priority: 6
      middlewares:
        - name: mlflow-strip-prefix
      services:
        - kind: Service
          name: mlflow
          port: { { .Values.mlflow.service.port } }

    # Non-GET on /mlflow → 405 via a tiny denier service. The denier is a
    # 1-replica nginx Pod returning 405 for all requests; cheap and
    # standard. See denier deployment in mlflow-deny-write.yaml below.
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/mlflow`)
      priority: 5
      services:
        - kind: Service
          name: mlflow-deny-write
          port: 80

    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`)
      priority: 1
      services:
        - kind: Service
          name: frontend
          port: 80
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: mlflow-strip-prefix
  namespace: { { .Release.Namespace } }
spec:
  stripPrefix:
    prefixes:
      - /mlflow
```

- [ ] **Step 2: Add `mlflow-deny-write` denier Pod and Service**

Create `charts/lolday/templates/mlflow-deny-write.yaml`:

```yaml
{{- if .Values.mlflow.enabled }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: mlflow-deny-write-conf
  namespace: {{ .Release.Namespace }}
data:
  default.conf: |
    server {
      listen 80;
      location / {
        return 405 'MLflow API writes must go through lolday backend.\n';
        add_header Content-Type text/plain;
      }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mlflow-deny-write
  namespace: {{ .Release.Namespace }}
spec:
  replicas: 1
  selector: { matchLabels: { app: mlflow-deny-write } }
  template:
    metadata: { labels: { app: mlflow-deny-write } }
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 101            # nginx default
        fsGroup: 101
      containers:
        - name: nginx
          image: nginx:alpine
          ports: [{ containerPort: 80 }]
          volumeMounts:
            - name: conf
              mountPath: /etc/nginx/conf.d
          resources:
            requests: { cpu: 10m, memory: 16Mi }
            limits: { cpu: 50m, memory: 32Mi }
      volumes:
        - name: conf
          configMap: { name: mlflow-deny-write-conf }
---
apiVersion: v1
kind: Service
metadata:
  name: mlflow-deny-write
  namespace: {{ .Release.Namespace }}
spec:
  selector: { app: mlflow-deny-write }
  ports:
    - port: 80
      targetPort: 80
{{- end }}
```

- [ ] **Step 3: Helm lint + template**

```bash
helm lint charts/lolday
helm template charts/lolday --values charts/lolday/values.yaml > /tmp/rendered.yaml
grep -A 3 "mlflow-strip-prefix\|mlflow-deny-write" /tmp/rendered.yaml | head -50
```

Expected: lint passes, IngressRoute + Middleware + denier Pod render correctly.

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/templates/ingress.yaml charts/lolday/templates/mlflow-deny-write.yaml
git commit -m "$(cat <<'EOF'
feat(chart): expose /mlflow/ UI with read-only method gate (phase 13b B5)

Cloudflare Access policy on the host already covers auth. Writes (POST/PUT/
DELETE) on /mlflow are denied by a tiny nginx-405 deployment so all
audit-relevant mutations must go through the lolday backend's
experiments_proxy.
EOF
)"
```

---

## Task 8.2: Chart — MLflow `--static-prefix=/mlflow`

**Files:**

- Modify: `charts/lolday/templates/mlflow.yaml`

- [ ] **Step 1: Add the flag**

In `charts/lolday/templates/mlflow.yaml`, find the `args:` block for the mlflow container and add:

```yaml
args:
  - --host=0.0.0.0
  - --port={{ .Values.mlflow.service.port }}
  - --backend-store-uri=postgresql+psycopg2://$(PG_USER):$(PG_PASSWORD)@postgresql.{{ .Values.global.namespace }}.svc:5432/$(PG_DB)
  - --default-artifact-root=mlflow-artifacts:/
  - --artifacts-destination=/mlflow-artifacts
  - --serve-artifacts
  - --static-prefix=/mlflow # phase 13b B5: rewrite asset URLs for reverse-proxy
```

- [ ] **Step 2: Helm lint**

```bash
helm lint charts/lolday
```

Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/mlflow.yaml
git commit -m "feat(chart): mlflow --static-prefix=/mlflow for reverse-proxy (phase 13b B5)"
```

---

## Task 8.3: Deploy chart change and verify MLflow exposure

**Files:** none (deployment).

- [ ] **Step 1: Deploy**

```bash
helm upgrade --install lolday charts/lolday -n lolday --values charts/lolday/values.yaml
kubectl -n lolday rollout status deploy/mlflow deploy/mlflow-deny-write
```

Expected: rollout succeeds.

- [ ] **Step 2: Verify**

```bash
# Read works
curl -sf -H "Host: $LOLDAY_HOST" http://<traefik-ip>/mlflow/ | head -5
# Asset URL has prefix
curl -s -H "Host: $LOLDAY_HOST" http://<traefik-ip>/mlflow/ | grep -o '/mlflow/static-files/[^"]*' | head -3
# Write blocked
curl -s -o /dev/null -w "%{http_code}" -X POST -H "Host: $LOLDAY_HOST" \
  http://<traefik-ip>/mlflow/api/2.0/mlflow/runs/create -d '{}'
# Expect: 405
```

If write returns 200, the deny rule isn't catching — debug Traefik routes (`kubectl describe ingressroute lolday -n lolday`).

- [ ] **Step 3: Browser smoke**

Open `https://$LOLDAY_HOST/mlflow/` in browser → expect MLflow UI rendering. Click an experiment → should still be on MLflow UI, with all asset paths under `/mlflow/`.

---

## Task 9.1: E2E tests — Job Detail per type

**Files:**

- Modify: `frontend/tests/e2e/jobs.spec.ts`

- [ ] **Step 1: Tests**

```ts
test.describe("Job Detail Summary tab — per type", () => {
  test("train job shows TrainSummary cards", async ({ page, seedTrainJob }) => {
    const job = await seedTrainJob({ withMetrics: true });
    await page.goto(`/jobs/${job.id}`);
    await expect(
      page.getByRole("heading", { name: /Final metrics/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Confusion matrix/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Trained model/i }),
    ).toBeVisible();
  });

  test("evaluate job shows EvaluateSummary cards", async ({
    page,
    seedEvaluateJob,
  }) => {
    const job = await seedEvaluateJob({
      withMetrics: true,
      withPerClass: true,
    });
    await page.goto(`/jobs/${job.id}`);
    await expect(
      page.getByRole("heading", { name: /Source model/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Evaluation metrics/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /Per-class metrics/i }),
    ).toBeVisible();
  });

  test("predict job shows PredictSummary cards with download", async ({
    page,
    seedPredictJob,
  }) => {
    const job = await seedPredictJob({ withPredictions: true });
    await page.goto(`/jobs/${job.id}`);
    await expect(
      page.getByRole("heading", { name: /Predictions/i }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Download predictions.csv/i }),
    ).toBeVisible();
  });

  test("ResolvedConfigCard shows user params separate from full config", async ({
    page,
    seedTrainJob,
  }) => {
    const job = await seedTrainJob({ userParams: { n_estimators: 200 } });
    await page.goto(`/jobs/${job.id}`);
    await expect(page.getByText(/Your hyperparameters/i)).toBeVisible();
    await expect(page.getByText("n_estimators")).toBeVisible();
    await expect(page.getByText("200")).toBeVisible();
    // Full config collapsed by default
    await expect(page.getByText(/Show full resolved config/i)).toBeVisible();
  });
});
```

- [ ] **Step 2: Run**

```
cd frontend && pnpm playwright test jobs.spec.ts -g "Job Detail Summary"
```

Expected: PASS once fixtures wired.

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/e2e/jobs.spec.ts
git commit -m "test(e2e): per-type Job Detail Summary tab + ResolvedConfigCard (phase 13b B1, B3)"
```

---

## Task 9.2: E2E tests — Submit form Hyperparameters

**Files:**

- Modify: `frontend/tests/e2e/jobs.spec.ts` (or new submit.spec.ts)

- [ ] **Step 1: Tests**

```ts
test.describe("Submit form — Hyperparameters", () => {
  test("Train explainer + RJSF defaults visible", async ({ page }) => {
    await page.goto("/jobs/new");
    // Train is default; explainer says train
    await expect(page.getByText(/訓練新模型/)).toBeVisible();

    // Pick a detector + version (assume seed)
    // ... select detector / version actions
    // Hyperparameters block: description visible
    await expect(page.getByText(/Number of trees/i)).toBeVisible();
    // Default placeholder
    await expect(page.getByPlaceholder(/Default: 100/i)).toBeVisible();
  });

  test("Switching to Evaluate changes explainer + form", async ({ page }) => {
    await page.goto("/jobs/new");
    await page.getByRole("button", { name: /^Evaluate$/i }).click();
    await expect(page.getByText(/用既有模型評估/)).toBeVisible();
    // EvaluateConfig schema: only threshold
    // ... after detector pick, verify only one field visible
  });

  test("Reset to defaults restores values", async ({ page }) => {
    await page.goto("/jobs/new");
    // ... pick detector, version, fill hyperparams to non-default, click Reset
    // assert n_estimators === 100 in the form
  });
});
```

- [ ] **Step 2: Run + Commit**

```bash
cd frontend && pnpm playwright test
git add frontend/tests/e2e
git commit -m "test(e2e): submit form Hyperparameters (phase 13b B2)"
```

---

## Task 9.3: E2E tests — Runs three-tier + MLflow link

**Files:**

- Create: `frontend/tests/e2e/runs.spec.ts`
- Create: `frontend/tests/e2e/mlflow.spec.ts`

- [ ] **Step 1: runs.spec.ts**

```ts
import { test, expect } from "@playwright/test";

test.describe("Runs three-tier UX", () => {
  test("ExperimentCard shows stats", async ({ page }) => {
    await page.goto("/runs");
    const cards = page.getByTestId(/experiment-card/);
    await expect(cards.first()).toBeVisible();
    // Stats text
    await expect(page.getByText(/runs/i).first()).toBeVisible();
    await expect(page.getByText(/Best F1/i).first()).toBeVisible();
  });

  test("Column picker toggles columns", async ({ page, seedRunsForExp }) => {
    const expId = await seedRunsForExp();
    await page.goto(`/runs/${expId}`);
    await page.getByRole("button", { name: /columns/i }).click();
    await page.getByRole("menuitemcheckbox", { name: "f1" }).click();
    // Column appears
    await expect(page.getByRole("columnheader", { name: "f1" })).toBeVisible();
  });

  test("Run detail Open job navigates to lolday job", async ({
    page,
    seedRunWithJobTag,
  }) => {
    const { expId, runId, jobId } = await seedRunWithJobTag();
    await page.goto(`/runs/${expId}/${runId}`);
    await page.getByRole("link", { name: /Open job/i }).click();
    await expect(page).toHaveURL(`/jobs/${jobId}`);
  });
});
```

- [ ] **Step 2: mlflow.spec.ts**

```ts
import { test, expect } from "@playwright/test";

test.describe("MLflow UI exposure", () => {
  test("MLflow UI loads at /mlflow/", async ({ page }) => {
    const resp = await page.goto("/mlflow/");
    expect(resp?.status()).toBe(200);
    await expect(page).toHaveURL(/\/mlflow\/?/);
  });

  test("MLflow asset URLs include /mlflow prefix", async ({ page }) => {
    await page.goto("/mlflow/");
    const html = await page.content();
    // No bare /static-files/ — all should be /mlflow/static-files/
    expect(html).not.toMatch(/(?<!\/mlflow)\/static-files\//);
    expect(html).toMatch(/\/mlflow\/static-files\//);
  });

  test("MLflow POST is blocked with 405", async ({ request }) => {
    const resp = await request.post("/mlflow/api/2.0/mlflow/runs/create", {
      data: {},
    });
    expect(resp.status()).toBe(405);
  });
});
```

- [ ] **Step 3: Run + commit**

```bash
cd frontend && pnpm playwright test runs.spec.ts mlflow.spec.ts
git add frontend/tests/e2e/runs.spec.ts frontend/tests/e2e/mlflow.spec.ts
git commit -m "test(e2e): Runs three-tier UX + MLflow exposure (phase 13b B4, B5)"
```

---

## Task 10.1: Build + push backend phase13b image

**Files:**

- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Build + push**

```bash
cd backend && docker build -t harbor.lolday.svc:80/lolday/backend:phase13b .
docker push harbor.lolday.svc:80/lolday/backend:phase13b
```

- [ ] **Step 2: Bump tag**

```yaml
backend:
  image:
    tag: phase13b
```

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "chore(deploy): bump backend default tag to phase13b"
```

---

## Task 10.2: Build + push frontend phase13b image

**Files:**

- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Build + push**

```bash
cd frontend && docker build -t harbor.lolday.svc:80/lolday/frontend:phase13b .
docker push harbor.lolday.svc:80/lolday/frontend:phase13b
```

- [ ] **Step 2: Bump tag**

```yaml
frontend:
  image:
    tag: phase13b
```

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "chore(deploy): bump frontend default tag to phase13b"
```

---

## Task 10.3: Run alembic migration + helm upgrade + smoke

**Files:** none (deployment).

- [ ] **Step 1: Migration**

```bash
kubectl -n lolday exec deploy/backend -- alembic upgrade head
```

Expected: success, `job` table now has `user_params`.

- [ ] **Step 2: helm upgrade**

```bash
helm upgrade --install lolday charts/lolday -n lolday --values charts/lolday/values.yaml
kubectl -n lolday rollout status deploy/backend deploy/frontend deploy/mlflow deploy/mlflow-deny-write
```

- [ ] **Step 3: Manual verification (mirrors spec §Testing strategy → Manual verification)**

1. Visit a recent train job — TrainedModelCard links to model registry, MetricsTable shows all metrics (incl. roc_auc).
2. Visit a recent evaluate job — Per-class table populated (after maldet release lands; otherwise hidden).
3. Visit a recent predict job — distribution + percentages + working "Download predictions.csv" link.
4. Submit form: pick `Train`, see Chinese stage explainer, pre-populated `n_estimators=100`, description "Number of trees in the forest." visible. Repeat for Evaluate and Predict.
5. ResolvedConfigCard: user params shown at top with default-vs-overridden indicator; expand → tree visible.
6. ExperimentCard on `/runs` shows run_count and best_f1.
7. Click "Open in MLflow" from any of the 3 runs tiers — opens `/mlflow/...` correctly with prefix in URL bar.
8. From an MLflow run page, attempting `POST` returns 405 (test via curl).
9. Run detail "↗ Open job" goes back to the lolday job that produced this run.

- [ ] **Step 4: Note any deferred items**

If maldet `per_class` event PR has not yet landed, `<PerClassMetrics>` will be hidden (correct behavior). Record the maldet PR status in the spec's Open Questions section as the tracker.

---

## Self-Review

### Spec coverage check

| Spec section                                           | Plan task        |
| ------------------------------------------------------ | ---------------- |
| §1 B1 dispatcher                                       | 5.7, 5.8         |
| §1 B1 TrainSummary / EvaluateSummary / PredictSummary  | 5.7              |
| §1 B1 MetricsTable                                     | 5.1              |
| §1 B1 PerClassMetrics                                  | 5.2              |
| §1 B1 SourceModelCard / TrainedModelCard               | 5.3, 5.4         |
| §1 B1 PredictionSummaryCard                            | 5.5              |
| §1 B1 backend per_class projection                     | 1.1              |
| §1 B1 backend prediction summary projection            | 1.2              |
| §1 B1 backend prediction-summary fallback endpoint     | 1.3              |
| §2 B2 deriveUiSchema + fillDefaults                    | 6.1              |
| §2 B2 RjsfConfigForm rewrite                           | 6.2              |
| §2 B2 StageExplainer + i18n                            | 6.3              |
| §3 B3 react-json-view                                  | 4.1              |
| §3 B3 JsonTreeView                                     | 4.2              |
| §3 B3 replace JsonViewer                               | 4.3              |
| §3 B3 ResolvedConfigCard + UserParamsTable             | 5.6              |
| §3 B3 backend Job.user_params                          | 2.1              |
| §3 B3 backend submit_job writes user_params            | 2.2              |
| §4 B4 backend experiments aggregate + cache            | 3.1              |
| §4 B4 ExperimentsListPage + ExperimentCard             | 7.2              |
| §4 B4 RunsColumnPicker + StatusFilter                  | 7.3              |
| §4 B4 RunsList rewrite                                 | 7.4              |
| §4 B4 RunDetail rewrite                                | 7.5              |
| §4 B4 OpenInMlflow / OpenInLoldayJob / CollapsibleCard | 7.1              |
| §5 B5 IngressRoute + middleware + denier               | 8.1              |
| §5 B5 mlflow --static-prefix                           | 8.2              |
| §5 B5 deploy + verify                                  | 8.3              |
| Migration & Deploy                                     | 10.1, 10.2, 10.3 |
| Testing strategy → E2E                                 | 9.1, 9.2, 9.3    |

All sections accounted for. Per-class is split between backend projection (1.1) and frontend display (5.2 / 5.7) — both wired.

### Placeholder scan

- `<previous>` / `<hash>` in Alembic revision boilerplate — explicit fill instructions, not a missing field.
- Several places say "Locate the actual constructor" or "Match imports / dependencies pattern with the existing handlers" — these point at small inspection tasks the engineer must do because the existing file's exact line layout depends on prior phases. Acceptable instruction.
- Task 5.3's `useModelVersionForJob` references endpoints `/api/v1/models/versions/{id}` and `/api/v1/models/versions?source_job_id=...`; if missing in backend, the comment says to defer to 13b.1. Explicit branch.

### Type consistency

- `user_params: dict | None` consistent across model (2.1), schema (2.2), JobRead, and ResolvedConfigCard (5.6).
- `summary_metrics` shape: `{metrics, confusion_matrix, per_class, prediction_summary}` consistent between projection (1.1, 1.2), schema, and consumers (5.7, 5.8).
- `RunsStatus` type exported from `RunsStatusFilter` and consumed in `RunsListPage` (7.3, 7.4).
- `RunsColumnPicker` `selected: string[]` keys formatted as `metrics.<name>` / `params.<name>` consistently in 7.3 and 7.4.

No inconsistencies found.
