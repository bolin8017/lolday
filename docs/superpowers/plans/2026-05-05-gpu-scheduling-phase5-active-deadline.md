# Phase 5: per-job `active_deadline_seconds` override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users opt into a longer per-job deadline (e.g. 24h training) instead of being forced into the global 6h cap. Closes spec §7 Phase 5.

**Architecture:** New nullable `active_deadline_seconds` column on `Job`; `JobCreate` accepts an optional override and validates against per-type MAX env vars; if `None`, falls back to the existing global default. No role-based gating (the per-type MAX is the cap for everyone — simpler than admin/user split, matches "any user can run a 24h training" UX). Per-type MAX env vars default to: train 24h, evaluate 2h, predict 4h — comfortably above the existing defaults (6h / 30m / 1h) but below the SLO ceiling.

**Tech Stack:** alembic add column (nullable, no backfill), Pydantic v2 model_validator, FastAPI sync handler.

**Spec:** §7 Phase 5.

**Pre-requisite:** Phase 0–4 merged + applied. This is the last phase; deploy bundles all Phase 2/3/5 backend code via the next backend image rebuild.

---

## File map

**New:**

- `backend/migrations/versions/<rev>_job_active_deadline_seconds.py` — alembic.
- `backend/tests/test_routers_jobs_active_deadline.py` — 4 unit tests (default, override, exceed-max, negative).
- `tests/2026-05-05-phase5-active-deadline-smoke.sh`.

**Modified:**

- `backend/app/models/job.py` — add `active_deadline_seconds: Mapped[int | None]`.
- `backend/app/config.py` — `JOB_ACTIVE_DEADLINE_TRAIN_MAX_SECONDS=86400` (24h), `JOB_ACTIVE_DEADLINE_EVALUATE_MAX_SECONDS=7200` (2h), `JOB_ACTIVE_DEADLINE_PREDICT_MAX_SECONDS=14400` (4h).
- `backend/app/schemas/job.py` — `JobCreate.active_deadline_seconds: int | None = None` + `model_validator` rejecting > MAX or <= 0.
- `backend/app/services/job_spec.py::_active_deadline` — accept optional override; signature now `_active_deadline(job_type, override)`.
- `backend/app/routers/jobs.py::create_job` — read `body.active_deadline_seconds`, persist on Job row, pass to `build_volcano_job_manifest` via a new kwarg.
- `frontend/src/api/schema.gen.ts` — hand-stitch `active_deadline_seconds?: number | null` on `JobCreate`.

**Not touched:** chart (no env var change), reconciler (already reads `activeDeadlineSeconds` from vcjob status verbatim).

---

## Tasks

### Task 1 — config.py

```python
JOB_ACTIVE_DEADLINE_TRAIN_SECONDS: int = 21600  # 6h (default)
JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS: int = 1800  # 30m (default)
JOB_ACTIVE_DEADLINE_PREDICT_SECONDS: int = 3600  # 1h (default)
# Phase 5 — per-job override caps. User-supplied
# active_deadline_seconds must be <= the matching MAX.
JOB_ACTIVE_DEADLINE_TRAIN_MAX_SECONDS: int = 86400  # 24h
JOB_ACTIVE_DEADLINE_EVALUATE_MAX_SECONDS: int = 7200  # 2h
JOB_ACTIVE_DEADLINE_PREDICT_MAX_SECONDS: int = 14400  # 4h
```

### Task 2 — models/job.py

Add column after `summary_metrics`:

```python
    active_deadline_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
```

Make sure `from sqlalchemy import ...` includes `Integer` (likely already imported).

### Task 3 — alembic

Auto-gen filename: `<rev>_job_active_deadline_seconds.py`. Body:

```python
def upgrade() -> None:
    """Phase 5 — per-job active_deadline_seconds override."""
    op.add_column(
        "job",
        sa.Column("active_deadline_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job", "active_deadline_seconds")
```

### Task 4 — schemas/job.py

```python
class JobCreate(BaseModel):
    ...
    resource_profile: ResourceProfile = ResourceProfile.STANDARD
    # Phase 5 — optional per-job timeout override. None → use the per-type
    # default (config.JOB_ACTIVE_DEADLINE_*_SECONDS). Caps validated below.
    active_deadline_seconds: int | None = None

    @model_validator(mode="after")
    def _validate_active_deadline(self) -> "JobCreate":
        if self.active_deadline_seconds is None:
            return self
        if self.active_deadline_seconds <= 0:
            raise ValueError("active_deadline_seconds must be > 0")
        from app.config import settings  # local import to avoid cycle
        max_by_type = {
            JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_MAX_SECONDS,
            JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_MAX_SECONDS,
            JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_MAX_SECONDS,
        }
        cap = max_by_type[self.type]
        if self.active_deadline_seconds > cap:
            raise ValueError(
                f"active_deadline_seconds ({self.active_deadline_seconds}) "
                f"exceeds max for {self.type.value} ({cap})"
            )
        return self
```

> Note: this validator must run _after_ `_validate_refs_per_type` if both exist (Pydantic runs `mode="after"` in declaration order). Independent of refs validation, so order doesn't matter.

### Task 5 — services/job_spec.py

```python
def _active_deadline(
    job_type: JobType, override: int | None = None
) -> int:
    if override is not None:
        return override
    return {
        JobType.TRAIN: settings.JOB_ACTIVE_DEADLINE_TRAIN_SECONDS,
        JobType.EVALUATE: settings.JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS,
        JobType.PREDICT: settings.JOB_ACTIVE_DEADLINE_PREDICT_SECONDS,
    }[job_type]
```

`build_volcano_job_manifest` adds `active_deadline_seconds: int | None = None` kwarg and passes it through:

```python
def build_volcano_job_manifest(
    *,
    ...
    queue_name: str,
    active_deadline_seconds: int | None = None,
    ...
) -> dict[str, Any]:
    ...
    pod_spec = {
        "activeDeadlineSeconds": _active_deadline(job_type, active_deadline_seconds),
        ...
    }
```

### Task 6 — routers/jobs.py

Persist override on Job row + pass to manifest:

Around the `Job(...)` constructor (line ~330):

```python
    job = Job(
        ...
        resource_profile=body.resource_profile,
        active_deadline_seconds=body.active_deadline_seconds,
    )
```

In the `build_volcano_job_manifest(...)` call (line ~352):

```python
    manifest = build_volcano_job_manifest(
        ...
        active_deadline_seconds=body.active_deadline_seconds,
        ...
    )
```

### Task 7 — backend tests

`backend/tests/test_routers_jobs_active_deadline.py`:

```python
"""Phase 5 — per-job active_deadline_seconds override."""

import pytest
from pydantic import ValidationError

from app.models.job import JobType
from app.schemas.job import JobCreate


def _base_kwargs(jt: JobType = JobType.TRAIN) -> dict:
    import uuid
    base = {
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
    # Evaluate cap is 7200 (2h); 8000 is above evaluate cap but below
    # train cap — checks that the cap is type-aware.
    with pytest.raises(ValidationError, match="exceeds max"):
        JobCreate(**_base_kwargs(JobType.EVALUATE), active_deadline_seconds=8000)
```

Plus 1 test in `test_services_job_spec_phase11b.py`:

```python
def test_active_deadline_override_passes_through() -> None:
    """Phase 5 — per-job override populates spec.template.spec.activeDeadlineSeconds."""
    m = build_volcano_job_manifest(
        job_id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        job_type=JobType.TRAIN,
        detector_image="x",
        mlflow_experiment_id="e1",
        mlflow_run_id="r1",
        mlflow_tracking_uri="x",
        source_run_id=None,
        source_artifact_path=None,
        internal_events_url="x",
        queue_name="lolday-u-test",
        active_deadline_seconds=43200,
    )
    pod_spec = m["spec"]["tasks"][0]["template"]["spec"]
    assert pod_spec["activeDeadlineSeconds"] == 43200
```

### Task 8 — frontend hand-stitch

`frontend/src/api/schema.gen.ts` — find `JobCreate`, add the optional field:

```diff
         JobCreate: {
             ...
             resource_profile?: components["schemas"]["ResourceProfile"];
+            /** Active Deadline Seconds */
+            active_deadline_seconds?: number | null;
         };
```

### Task 9 — smoke test

`tests/2026-05-05-phase5-active-deadline-smoke.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
NS=${NS:-lolday}
fail=0

echo "[step 1/2] job table has active_deadline_seconds column"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import asyncio, os, asyncpg
async def main():
    url = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    conn = await asyncpg.connect(url)
    row = await conn.fetchrow(\"SELECT column_name FROM information_schema.columns WHERE table_name='job' AND column_name='active_deadline_seconds'\")
    print('OK' if row else 'FAIL')
asyncio.run(main())
" 2>/dev/null || true)
case "${out}" in
  OK) echo "OK" ;;
  *) echo "FAIL: column missing"; fail=1 ;;
esac

echo ""
echo "[step 2/2] OpenAPI schema accepts optional active_deadline_seconds"
out=$(kubectl -n "${NS}" exec deploy/backend -c backend -- python3 -c "
import json, urllib.request
d = json.load(urllib.request.urlopen('http://localhost:8000/openapi.json'))
print('OK' if 'active_deadline_seconds' in d['components']['schemas']['JobCreate']['properties'] else 'FAIL')
" 2>/dev/null || true)
case "${out}" in
  OK) echo "OK" ;;
  *) echo "FAIL: JobCreate schema missing field"; fail=1 ;;
esac

echo ""
[ "${fail}" -eq 0 ] && echo "=== SMOKE PASSED ===" || { echo "=== SMOKE FAILED ==="; exit 1; }
```

### Task 10 — pre-commit + commit + push + PR

Standard. Title: `feat(backend, frontend): phase 5 — per-job active_deadline_seconds override`.

### Task 11 — deploy (operator-attended)

helm upgrade ships migration via `alembic-upgrade-hook` Job. Backend code change requires backend image rebuild — bundles with Phase 2 + Phase 3 backend changes (one rebuild covers all).

---

## Self-review checklist

- [ ] migration adds nullable column (no backfill, no NOT NULL — old jobs stay None).
- [ ] downgrade drops the column cleanly.
- [ ] validator rejects > MAX, <= 0, but accepts None and any valid in-range int.
- [ ] cap is type-aware (train/evaluate/predict each have their own MAX).
- [ ] manifest builder passes the override through to the pod spec.
- [ ] full backend pytest passes (target: 492 + 5 new = 497).
