# Phase 11e: Typed Detector Contract — Design Specification

## Overview

Phase 11d.1 closed the immediate live-metric-chart bug, but it surfaced two latent
gaps from the original Phase 11 contract design:

1. **Hyperparameters are an opaque dict.** `params: dict[str, Any]` flows from
   the platform UI into the detector with no shape declaration. The frontend
   has no schema to render a typed form, so users edit a free-form JSON
   textarea. The backend's `validate_user_params` is a shallow guard
   (rejects extras for known keys, coerces a few types) and cannot tell
   whether `lr=0.01` is a valid hyperparameter for a given stage.
2. **`job.summary_metrics` has three competing sources of truth.** The
   `job_events` table holds every metric event, MLflow holds the run's
   metrics, and the `summary_metrics` JSONB column was meant to be a
   denormalized cache — but nothing populates it, so the "Metrics" summary
   card on `/jobs/:id` always reads "No metrics recorded yet."

Phase 11e closes both gaps with one cohesive contract change. The maldet
manifest becomes the typed boundary between detectors and the platform: it
declares a JSON Schema for each stage's user-overridable params, and the
backend validates user input against that schema. The reconciler projects
`job_events` into `job.summary_metrics` on `stage_end`, making the events
table the single source of truth for run-time metrics with the column
serving as a materialized read model for the list and detail UIs.

**Goal:** A detector author writes a Pydantic config class (as today). At
build time, `maldet introspect-schema` emits a JSON Schema into the manifest.
The platform reads that schema, validates user-submitted params at submit
time, and renders an RJSF form on the job-submission page. After job
completion, the reconciler aggregates the latest metric events into the job's
`summary_metrics`; both the job list and the detail page consume that
projection.

**Authorization:** Breaking changes are explicitly approved. Phase 11d.1
detector versions (elfrfdet v2.0.6, elfcnndet v2.1.0) will not run after
phase 11e backend deploy — they must be rebuilt as v3.0.0 with maldet 1.1.

---

## Scope

### In scope

1. **maldet 1.1.0**: typed-config introspection (`maldet introspect-schema`),
   `DetectorManifest.Stage.params_schema` required field, `confusion_matrix`
   event kind, `maldet check` lint enforcing `extra="forbid"` on stage
   config classes.
2. **elfrfdet 3.0.0 and elfcnndet 3.0.0**: maldet 1.1 dependency, stage
   config classes converted to explicit Pydantic `BaseModel` with
   `extra="forbid"`, CHANGELOG bumps, image rebuild.
3. **Lolday backend phase11e**: `VersionDetailRead.manifest` exposes the
   stored manifest; `validate_user_params` replaced by `jsonschema`
   validation against `manifest.stages.{stage}.params_schema`; reconciler
   projects events into `summary_metrics` on `stage_end`; `JobSummary`
   exposes `summary_metrics`; the hand-rolled `services/jobs_params_guard.py`
   is deleted.
4. **Lolday frontend phase11e**: `JobSubmitForm` renders RJSF from the new
   manifest schema; `_authed.detectors.$id.tsx` exposes a "View manifest"
   sheet; `/jobs` list adds a "Final metrics" tile column;
   `_authed.jobs.$id.tsx` Summary card now has data; the JSON-textarea
   path and `parseParams` helper are deleted.
5. **One-shot backfill script** (`scripts/backfill-summary-metrics.py`) so
   the 21 retained completed jobs from phase 11d also get
   `summary_metrics` populated. Optional, off the critical-path.

### Out of scope (explicitly deferred)

- **`primary_metric` concept**. The list-page tile shows the first two
  metrics alphabetically; declaring which metric is primary in the manifest
  is a phase 11f cosmetic enhancement, not a structural one.
- **Schema-aware sort/filter on the list page.** `/jobs` list shows the
  tile but doesn't sort by metric or filter on metric thresholds.
- **`metrics_schema` in the manifest.** Maldet's event kinds already type
  metric events (`name: str, value: float, step: int|None`); declaring
  per-detector "expected metrics" is YAGNI.
- **Multi-node distributed training** and **online serving** remain
  deferred per the original Phase 11 spec.

---

## Architecture

### Data flow

```
DETECTOR SIDE (maldet 1.1)
  Pydantic config classes   ──[model.model_json_schema()]──┐
  (extra="forbid")                                          ▼
  maldet.toml + Hydra config tree ──[maldet build]──> manifest.json
                                                       (含 stages.{stage}.params_schema)
                                                          │
                                                          ▼ OCI image label
PLATFORM SIDE (lolday phase11e)
  Harbor pull manifest ──> DetectorVersion.manifest (JSONB)
                              ├──[jsonschema.Draft202012Validator]──> user_params 422 rejection
                              └──[VersionDetailRead.manifest]──> RJSF form

  job_events (kind=metric, kind=confusion_matrix) = SINGLE SOURCE OF TRUTH
                              ├──[time-series view]──> Live metrics chart
                              ├──[reconciler 在 stage_end aggregate]──> job.summary_metrics
                              │                                          ├──> JobSummary
                              │                                          │     (list 頁 tile)
                              │                                          └──> JobRead
                              │                                                (Summary card)
                              └──[useJobEvents (existing)]──> chart 直供
```

### Invariants

1. **Hyperparameter shape** flows one direction: detector Pydantic class →
   manifest schema → backend validation → frontend form. There is no
   second source; manifest is canonical.
2. **Run-time metrics**: `job_events` is canonical. `job.summary_metrics`
   is a _materialized read model_ with a single writer (the reconciler's
   `stage_end` projection). Re-running the projection produces identical
   output (idempotent).
3. **MLflow** remains the long-term metric history store but is **not**
   read by the lolday UI for any job display. The frontend reads either
   events directly (chart) or `summary_metrics` (cards). MLflow is for
   cross-job comparison, not single-job display.

---

## Section 1 — maldet 1.1.0

### Manifest schema additions

```python
# maldet/manifest.py
class Stage(BaseModel):
    hydra: Path
    model: ImportPath              # phase 11d
    params_schema: dict[str, Any]  # phase 11e — required
```

`DetectorManifest.model_validate` raises on manifests missing `params_schema`
in any declared stage. No backward-compatible fallback.

### `maldet introspect-schema` CLI command

New subcommand wired in `maldet/cli.py`. Invoked by `maldet build` before
the manifest is written.

**Convention enforced by phase 11e**: each stage has **one top-level Pydantic
config class** that nests model / trainer / data sub-configs. Detector
authors who today register multiple separate structured configs through
hydra-zen must wrap them into a single root class. This is the cost of
typing the contract; the rest of phase 11e relies on it.

Behavior:

1. Load `maldet.toml`; for each declared stage, locate the stage's root
   Hydra config file (e.g. `configs/train.yaml`).
2. Resolve that root config's `_target_` to a Python class — this is the
   stage's single top-level config class per the convention above.
3. Reject if the class is not a `pydantic.BaseModel` or if its
   `model_config` does not set `extra="forbid"`. Detector author gets a
   clear error naming the stage and class.
4. Call `cls.model_json_schema(mode="serialization")` (Pydantic v2).
5. Embed the resulting JSON Schema dict at
   `manifest["stages"][stage]["params_schema"]`.

### `confusion_matrix` event kind

```python
# maldet/events/kinds.py
class EventKind(StrEnum):
    ...
    CONFUSION_MATRIX = "confusion_matrix"

_REQUIRED_FIELDS[EventKind.CONFUSION_MATRIX] = ("labels", "matrix")
```

`BinaryClassification.evaluate` emits this once after `MetricReport` is
computed:

```python
logger.log_event(
    "confusion_matrix",
    labels=report.labels,        # ["benign", "malware"]
    matrix=report.confusion.tolist(),  # [[TN, FP], [FN, TP]]
)
```

### `maldet check` lint additions

`maldet check` now fails on:

- A stage whose top-level config class is not a `pydantic.BaseModel` (e.g.
  hydra-zen-built dataclass without explicit conversion).
- A stage whose top-level config class has `extra != "forbid"`.

Failure mode: non-zero exit + a clear message naming the offending stage
and class. CI for elfrfdet / elfcnndet gates on this.

### Templates

`templates/sklearn_basic/` and `templates/lightning_cnn/` updated:

- Stage config classes are explicit `pydantic.BaseModel` subclasses with
  `model_config = ConfigDict(extra="forbid")`.
- Hydra-zen `ZenStore` registrations point at these classes.

### Tests

| File                                          | Coverage                                                                                                                           |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_manifest_v11.py`                  | Old manifest (no `params_schema`) → `model_validate` raises. New manifest with valid schema → loads.                               |
| `tests/test_introspect_schema.py`             | Sample Pydantic class (nested + Literal + Optional) → JSON Schema with correct properties / required / additionalProperties=False. |
| `tests/test_check_lints_strict.py`            | `extra="allow"` config → check fails. dataclass-only config → check fails.                                                         |
| `tests/events/test_kinds_confusion_matrix.py` | Valid `confusion_matrix` event passes; missing `labels` or `matrix` raises.                                                        |
| `tests/integration/test_e2e_*` (existing)     | Re-run; verify produced `manifest.json` contains `params_schema`.                                                                  |

Coverage target: ≥ 80% (current 88%, new modules brought up to ≥ 80%).

### Release

PyPI `maldet 1.1.0`. CHANGELOG marks BREAKING for the new required field.
GitHub tag `v1.1.0`.

---

## Section 2 — Detector v3.0.0

Both `bolin8017/elfrfdet` and `bolin8017/elfcnndet` get a major bump.

### elfrfdet 3.0.0

- `pyproject.toml`: `maldet[mlflow]>=1.1,<2.0`.
- Stage config classes converted to explicit `pydantic.BaseModel` with
  `model_config = ConfigDict(extra="forbid")`:
  - `TrainConfig`: `n_estimators: int`, `max_depth: int | None`,
    `random_state: int`, etc.
  - `EvaluateConfig`: `threshold: float`.
  - `PredictConfig`: `batch_size: int`.
- CHANGELOG entry: 3.0.0 BREAKING — config classes promoted to typed
  Pydantic models for manifest schema generation.
- `tests/test_manifest.py`: import each stage's config class, drive a
  schema, assert key fields are present.
- CI runs `maldet check` and gates the release tag on it.

### elfcnndet 3.0.0

- `pyproject.toml`: `maldet[lightning,mlflow]>=1.1,<2.0`.
- Stage config classes the same: `TrainConfig` (epochs, batch_size, lr,
  embed_dim, hidden_dim, …), `EvaluateConfig`, `PredictConfig` — all
  Pydantic BaseModel + `extra="forbid"`.
- `ByteCNN.predict` / `predict_proba` (added in v2.1.0) unchanged.
- CHANGELOG entry: 3.0.0 BREAKING.
- Same `tests/test_manifest.py` pattern.

### Build pipeline

Unchanged. lolday's `/api/v1/detectors/.../builds` triggers a buildkit job
that runs `maldet build` inside the detector image; the resulting
`manifest.json` is embedded as an OCI label and read back by lolday's
`services/harbor.py` at registration time. Build-helper stays at v3,
job-helper stays at v4.

---

## Section 3 — Lolday backend phase11e

### Schemas

```python
# app/schemas/detector.py
class VersionDetailRead(VersionRead):
    manifest: dict[str, Any]      # phase 11e — full maldet 1.1 manifest

# app/schemas/job.py
class JobSummary(BaseModel):
    ...
    summary_metrics: dict[str, Any] | None  # phase 11e — exposed for list view
```

### `validate_user_params` rewrite

`services/jobs_params_guard.py` is deleted. Replacement lives in
`services/jobs_params_validate.py`:

```python
import jsonschema

def validate_user_params(
    *, params: dict[str, Any], schema: dict[str, Any]
) -> None:
    """Raise UserParamsRejected with JSON Pointer path on schema mismatch."""
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(params), key=lambda e: e.path)
    if not errors:
        return
    detail = "; ".join(
        f"{'/' + '/'.join(map(str, e.absolute_path)) or '/'}: {e.message}"
        for e in errors
    )
    raise UserParamsRejected(detail)
```

Caller in `routers/jobs.py:create_job`:

```python
schema = manifest_model.stages[body.type.value].params_schema
try:
    validate_user_params(params=body.params, schema=schema)
except UserParamsRejected as e:
    raise HTTPException(status_code=422, detail=str(e))
```

The pre-flight `DetectorManifest.model_validate(dv.manifest)` now naturally
rejects manifests missing `params_schema` (because maldet 1.1 marks it
required).

### Reconciler — summary projection

`app/reconciler.py` adds a step on `stage_end` events with `status=success`
(or any terminal state — projection is meaningful for failed jobs too as
long as some metric events exist):

```python
async def _project_summary_metrics(session, job_id):
    rows = (await session.execute(
        select(JobEvent.kind, JobEvent.payload, JobEvent.ts)
        .where(JobEvent.job_id == job_id)
        .where(JobEvent.kind.in_(["metric", "confusion_matrix"]))
        .order_by(JobEvent.ts.asc())
    )).all()

    metrics: dict[str, float] = {}
    confusion_matrix: dict | None = None
    for kind, payload, _ts in rows:
        if kind == "metric":
            metrics[payload["name"]] = payload["value"]
        elif kind == "confusion_matrix":
            confusion_matrix = {
                "labels": payload["labels"],
                "matrix": payload["matrix"],
            }
    job = await session.get(Job, job_id)
    job.summary_metrics = {"metrics": metrics, "confusion_matrix": confusion_matrix}
    await session.commit()
```

Failures don't block job termination — caught + logged + recorded as
`BACKEND_ERRORS{stage="summary_projection"}.inc()`.

### Backfill script (optional, off critical-path)

`scripts/backfill-summary-metrics.py`:

```python
# Selects job rows where status in terminal states and summary_metrics IS NULL,
# runs _project_summary_metrics for each. Idempotent.
```

User runs manually after deploy if they want the 21 audit-trail jobs to
populate.

### Deletions

- `app/services/jobs_params_guard.py` — gone
- `tests/test_services_jobs_params_guard.py` — gone
- `routers/jobs.py` import line for `jobs_params_guard` — gone

### Tests

| File                                          | Coverage                                                                                                                                                             |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/test_jsonschema_validate_params.py`    | Valid params pass; extras → 422 with `/path`; type mismatch → 422; out-of-range (`minimum`/`maximum`) → 422.                                                         |
| `tests/test_reconciler_summary_projection.py` | Insert metric+confusion_matrix events → trigger projection → assert shape. Re-run → idempotent. Job with no metric events → `{metrics: {}, confusion_matrix: None}`. |
| `tests/test_jobs_create_v11e.py`              | Mock detector with valid manifest+schema → submit valid params → 202. Submit invalid → 422 with JSON Pointer. Mock manifest missing schema → 400 (pre-flight).       |
| `tests/test_schemas_version_detail_read.py`   | `VersionDetailRead.model_fields` includes `manifest`; large schema serializes.                                                                                       |

---

## Section 4 — Lolday frontend phase11e

### `JobSubmitForm` — RJSF replaces JSON textarea

```tsx
// src/components/forms/JobSubmitForm.tsx
const stageSchema = versionDetail?.manifest?.stages?.[type]?.params_schema;

<Card>
  <CardHeader>
    <CardTitle>Hyperparameters</CardTitle>
  </CardHeader>
  <CardContent>
    {stageSchema ? (
      <RjsfConfigForm
        schema={stageSchema as object}
        value={config}
        onChange={setConfig}
      />
    ) : (
      <p className="text-sm text-destructive">
        Selected detector version has no params schema; rebuild with maldet ≥
        1.1.
      </p>
    )}
  </CardContent>
</Card>;
```

- `paramsText` state, `parseParams` helper, JSON textarea — all removed.
- Submit body sets `params: config` directly (already a typed dict).
- `RjsfConfigForm.tsx` (already in repo from before phase 11d.1) is the
  rendering component.

### Detector page — manifest viewer

`_authed.detectors.$id.tsx`: restore the per-version sheet, now showing the
full manifest:

```tsx
<Sheet>
  <SheetTrigger asChild>
    <Button variant="ghost" size="sm">
      View manifest
    </Button>
  </SheetTrigger>
  <SheetContent>
    <SheetHeader>
      <SheetTitle>Manifest: {tag}</SheetTitle>
    </SheetHeader>
    <JsonViewer value={data.manifest} />
  </SheetContent>
</Sheet>
```

`useDetectorVersion`, `JsonViewer` re-imported.

### `/jobs` list — Final metrics tile column

```tsx
{
  id: "final_metrics",
  header: "Final metrics",
  cell: ({ row }) => {
    const metrics = row.original.summary_metrics?.metrics ?? {};
    const entries = Object.entries(metrics);
    if (entries.length === 0)
      return <span className="text-muted-foreground">—</span>;
    const shown = entries.slice(0, 2);
    const more = entries.length - shown.length;
    return (
      <div className="flex gap-1 text-xs">
        {shown.map(([k, v]) => (
          <span key={k} className="rounded border px-1">
            {k}: {Number(v).toFixed(3)}
          </span>
        ))}
        {more > 0 && <span className="text-muted-foreground">+{more}</span>}
      </div>
    );
  },
}
```

### `/jobs/:id` detail page

- Summary card (line 32-34 of `_authed.jobs.$id.tsx`) reads `metrics`
  and `confusion_matrix` from `job.summary_metrics` — already does, just
  now has data.
- Live metrics chart visibility now depends on whether any event has
  `step >= 1`:

  ```tsx
  const hasTimeSeries = events.some(e => e.kind === "metric" && typeof e.step === "number" && e.step >= 1);
  ...
  {(hasTimeSeries || eventsError) && (
    <Card>
      <CardHeader><CardTitle>Live metrics</CardTitle></CardHeader>
      ...
    </Card>
  )}
  ```

  Evaluate / predict jobs (metrics without per-step series) hide the
  chart card; their metrics show in the Summary card instead.

### Schema regen

After backend phase11e is up, regen `frontend/src/api/schema.gen.ts` from
the running `/openapi.json`. Picks up `VersionDetailRead.manifest` and
`JobSummary.summary_metrics`.

### Deletions

- `parseParams` in `JobSubmitForm.logic.ts` — gone
- Corresponding tests in `JobSubmitForm.test.tsx` — gone

### Tests

| File                                               | Coverage                                                                                                                                                                                                               |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/components/JobSubmitForm.test.tsx`     | Mock `versionDetail.manifest.stages.train.params_schema` (epochs:int, lr:number, nested.dim:int) → RJSF renders fields → edit + submit → `useSubmitJob` sees nested typed `params`. Schema absent → "rebuild" message. |
| `tests/unit/components/JobsList.test.tsx`          | summary_metrics with metrics → tile + `+N more`. summary_metrics null → dash.                                                                                                                                          |
| `tests/unit/hooks/useJobEvents.test.ts` (existing) | unchanged.                                                                                                                                                                                                             |
| `tests/e2e/phase11e-full-flow.spec.ts`             | Opt-in `PHASE11E_VERIFY=1`. service token login → /jobs/new → pick v3.0.0 detector → RJSF renders → edit field → submit → wait stage_end → /jobs/:id sees chart + summary card → /jobs list sees tile.                 |

---

## Section 5 — Migration & Deploy

Strict ordering — each step blocks the next:

1. **maldet 1.1.0 release**: implement → tests pass → tag `v1.1.0` push →
   GitHub Actions publish to PyPI.
2. **Detector v3.0.0 releases** (parallel): elfrfdet 3.0.0 + elfcnndet
   3.0.0 — bump pin, run `maldet check`, run tests, tag `v3.0.0` push.
3. **Trigger lolday detector builds** for both v3.0.0 tags via
   `POST /api/v1/detectors/{id}/builds`. Verify the resulting
   `DetectorVersion.manifest` row contains `stages.train.params_schema`
   in DB.
4. **Backend phase11e build + push**: docker build → push to Harbor;
   `pytest` 416+ tests must pass.
5. **Frontend phase11e build + push**: `pnpm test`, `pnpm typecheck`,
   `pnpm build`, then docker build + push.
6. **`helm upgrade`** with both new images in a single transaction:
   ```
   helm -n lolday upgrade lolday charts/lolday \
     --reuse-values \
     --set backend.image=harbor.lolday.svc:80/lolday/lolday-backend:phase11e \
     --set frontend.image=harbor.lolday.svc:80/lolday/lolday-frontend:phase11e
   ```
7. **Smoke verification**:
   - `curl /api/v1/detectors/{id}/versions/v3.0.0` includes `manifest`
     with `stages.train.params_schema`.
   - Playwright `phase11e-full-flow.spec.ts` passes against deployed
     stack.
   - Submit a small predict job → wait `stage_end` → query DB:
     `SELECT summary_metrics FROM job WHERE id=...` is non-null.
   - Open `/jobs` list page → see tile.
8. **Bump `scripts/deploy.sh` defaults** to
   `lolday-backend:phase11e` and `lolday-frontend:phase11e` so future
   `helm upgrade` invocations don't need manual `--set` overrides.
9. **(optional)** Backfill: `uv run python scripts/backfill-summary-metrics.py`
   — repopulates the 21 retained audit-trail jobs.

### Risk windows

| Window                                  | Duration | Effect                                                                                                                                          | Mitigation                             |
| --------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| Step 4 push complete → Step 6 helm done | ~10 min  | Old v2.0.6/v2.1.0 detector job submission rejected (manifest missing schema).                                                                   | Don't submit during window.            |
| Step 6 helm rolling                     | ~30 sec  | One backend pod still phase11d2 while frontend phase11e — old backend can't return `manifest` field, frontend shows "rebuild detector" message. | helm rollingUpdate default; tolerable. |

### Rollback

```
helm -n lolday rollback lolday <previous-rev>
```

- Detector v2.0.6/v2.1.0 images stay in Harbor (not deleted).
- v3.0.0 images stay in Harbor (compatible with phase11d2 backend? No —
  phase11d2 backend's `DetectorManifest.model_validate` doesn't know about
  the new field but Pydantic ignores unknown fields by default, so it
  accepts. `validate_user_params` (the old hand-rolled one) still runs.
  Rollback is safe.)
- Already-written `summary_metrics` rows are harmless under phase11d2
  (frontend simply doesn't render the tile).

### maldet PyPI yank contingency

If 1.1.0 ships with a critical bug, yank PyPI + 1.1.1 patch + detector
rebuild + backend redeploy. Same path as 11a/11d patches; established.

---

## Section 6 — Testing strategy

### Per-layer coverage targets

| Layer           | Target                 | Current                                                       |
| --------------- | ---------------------- | ------------------------------------------------------------- |
| maldet          | ≥ 80%                  | 88% (will drop slightly with new modules; brought back ≥ 80%) |
| lolday backend  | every new module ≥ 80% | overall ~404 tests today, +12-15 in 11e                       |
| lolday frontend | every new module ≥ 80% | 36 unit + 1 opt-in e2e today, → ~45 unit + 2 opt-in e2e       |

### TDD order

Each unit-of-work follows red-green:

1. Write failing test referencing the desired API.
2. Verify the test fails for the expected reason.
3. Implement minimum to pass.
4. Verify all tests green.

### Cross-layer integration test

`tests/e2e/phase11e-full-flow.spec.ts` is the cross-layer canary. It
exercises detector → manifest → schema → form → submit → reconciler →
list-page tile. Opt-in (`PHASE11E_VERIFY=1`) so default CI doesn't need
a deployed cluster.

### Smoke checkpoints (gating each deploy step)

| Step | Gate                                                                                                                    |
| ---- | ----------------------------------------------------------------------------------------------------------------------- |
| 1    | `pip install maldet==1.1.0` + `maldet check elfrfdet/maldet.toml` exit 0                                                |
| 2    | Both detector repos' CI green + tags pushed                                                                             |
| 3    | `psql -tAc "SELECT manifest->'stages'->'train'->'params_schema' FROM detector_version WHERE git_tag='v3.0.0'"` non-null |
| 4    | `pytest` all green + image push OK                                                                                      |
| 5    | `pnpm test` + `pnpm build` + image push OK                                                                              |
| 6    | `kubectl rollout status deploy/{backend,frontend}` ready                                                                |
| 7    | Smoke (RJSF render, summary_metrics populated, tile visible) all pass                                                   |

---

## Open Questions

None at the time of writing; all design choices converged during
brainstorming. Implementation may surface unexpected detector-author
ergonomics (e.g. how to express defaults for `lr_schedule: dict | None`),
which will be resolved with point fixes during plan execution rather than
revisiting this spec.

---

## Appendix A — Sample manifest fragment (phase 11e)

```json
{
  "name": "elfrfdet",
  "version": "3.0.0",
  "stages": {
    "train": {
      "hydra": "configs/train.yaml",
      "model": "elfrfdet.models.make_rf",
      "params_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "n_estimators": { "type": "integer", "minimum": 1, "default": 100 },
          "max_depth": { "type": ["integer", "null"], "default": null },
          "random_state": { "type": "integer", "default": 42 }
        }
      }
    },
    "evaluate": {
      "hydra": "configs/evaluate.yaml",
      "params_schema": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "threshold": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "default": 0.5
          }
        }
      }
    }
  },
  "lifecycle": { "supports_distributed": false }
}
```

## Appendix B — Sample summary_metrics shape

```json
{
  "metrics": {
    "train_loss": 0.0123,
    "val_acc": 0.987,
    "f1": 0.94
  },
  "confusion_matrix": {
    "labels": ["benign", "malware"],
    "matrix": [
      [480, 12],
      [8, 292]
    ]
  }
}
```

`confusion_matrix` is `null` for jobs that don't run the evaluator (e.g.,
predict jobs) or where the evaluator did not emit one.
