# Phase 13b: Job Detail / Submit / Runs UX Redesign — Design Specification

## Overview

Phase 13a fixes the visible bugs blocking daily workflow. Phase 13b is the UX redesign: the parts that *work* but are hard to read or use.

Three problem clusters, all surfaced from real user feedback:

1. **Job Detail Summary tab is one-size-fits-none.** Train, evaluate, and predict jobs share a single Summary layout: a `Metrics` card with a hard-coded whitelist (`accuracy`, `precision`, `recall`, `f1`, `f1_score`), a `Confusion matrix` card, a `Live metrics` chart, and a `Resolved config` block. Predict jobs always show "No metrics recorded yet" (predict has no metrics by definition); evaluate jobs hide `roc_auc` and per-class breakdowns; train jobs lack a clear link to the trained model. Each stage has a different *thing the user came here to look at* — but the layout doesn't reflect that.
2. **Submit form Hyperparameters block reads as opaque.** `RjsfConfigForm` renders fields directly from the JSON Schema with no `uiSchema`, so:
   - Pydantic `Field(description=...)` annotations exist in the detector but never appear on screen.
   - Default values are not pre-populated; users see blank fields and don't realize defaults will be applied if left empty.
   - Field names are raw Python identifiers (`n_estimators`, `max_depth`).
   - There is no per-stage explanation of what train/evaluate/predict mean or which fields are required.
3. **Resolved config and Runs detail dump raw JSON.** `<JsonViewer>` is `<pre>{JSON.stringify(value, null, 2)}</pre>` — a 50-line wall of source code under the Summary card. Run detail tags and params have the same problem.

A fourth, related cluster: **the Runs section reinvents an inferior MLflow UI.** Lolday already runs an MLflow server, but its UI is not exposed; lolday's own Runs pages show two metric columns with no filter / sort / compare. The platform should let MLflow do what MLflow does well (compare runs, parameter parallel coordinates, metric over time), and focus its own Runs UI on what's lolday-specific (the bridge between an MLflow run and the lolday job that produced it).

Phase 13b addresses all four clusters with one cohesive UX redesign: per-type Job Detail layouts, an enhanced submit form with stage explainers, a tree-view-based config viewer, lightweight Runs improvements, and an exposed MLflow UI behind the existing Cloudflare Access policy.

**Authorization:** Breaking schema additions (`Job.user_params` column, `summary_metrics.per_class`, `summary_metrics.prediction_summary`) are explicitly approved. The maldet evaluator change to emit `per_class` event is an external dependency tracked separately.

---

## Scope

### In scope

1. **B1. Per-type Job Detail Summary tab** — split into `<TrainSummary>` / `<EvaluateSummary>` / `<PredictSummary>`, each tailored to that stage's salient information.
2. **B2. Submit form Hyperparameters polish** — RJSF uiSchema auto-derived from the JSON Schema (`description` → `ui:help`, `default` → placeholder), defaults pre-populated into `formData`, `<StageExplainer>` component, "Reset to defaults" button.
3. **B3. Resolved config / Run detail tree viewer** — replace `<JsonViewer>` with `<ResolvedConfigCard>` and a generic `<JsonTreeView>` (powered by `react-json-view`); split the Resolved config into a "Your hyperparameters" section (with default-vs-overridden indicators) and a collapsed full config tree.
4. **B4. Runs three-tier light overhaul** — experiments index with stats, runs list with column picker / sort / filter, run detail with tree view, "↗ Open in MLflow" buttons at every tier, "↗ Open job" link from run detail.
5. **B5. Expose MLflow UI** — add Traefik IngressRoute `/mlflow/`, configure MLflow `--static-prefix=/mlflow`, restrict to read-only methods (GET / HEAD / OPTIONS), inherit Cloudflare Access policy.

### Out of scope

- Hard delete (Phase 13a Q2: soft delete only).
- MLflow UI authentication beyond Cloudflare Access (MLflow has no built-in auth; CFA on the host is the boundary).
- Custom dashboards, sweeps, or W&B-class features (would reinvent MLflow's compare view).
- Replacing RJSF entirely with hand-coded forms (would defeat detector-self-describing manifest design).

---

## Architecture

### Cross-cutting changes

| Concern | Component / file | Why |
|---|---|---|
| Drop `<JsonViewer>` everywhere | `frontend/src/components/common/JsonViewer.tsx` deleted; replaced by `<JsonTreeView>` | Used in 3 places (manifest, resolved config, run params/tags) — all want tree view. |
| Add `react-json-view` dependency | `frontend/package.json` | Industry-standard JSON tree component (W&B, Streamlit, Insomnia use it). MIT licensed, no China-origin concerns. |
| Job model gains `user_params` | `backend/app/models/job.py` + migration | Needed to render "your params vs defaults" distinction in resolved config viewer. |
| `summary_metrics` JSON gains two new fields | `backend/app/reconciler.py` | `per_class` (evaluate) and `prediction_summary` (predict) — projected by reconciler at terminal transition. |
| MLflow exposed at `/mlflow/` | `charts/lolday/templates/ingress.yaml` + `mlflow.yaml` | Lets B4's "↗ Open in MLflow" buttons work. |

### Component dependency graph

```
                    JobDetailPage (router)
                    /        |         \
            TrainSummary  EvaluateSummary  PredictSummary
                |         |       |          |          |
                v         v       v          v          v
         MetricsTable  PerClass  Cm  PredictionSummary  ResolvedConfigCard
                                                         |
                                                         v
                                                  JsonTreeView
                                                  (also used in
                                                   detectors manifest,
                                                   runs params/tags)
```

```
JobSubmitForm
  |
  +-- StageExplainer (new)
  +-- RjsfConfigForm (enhanced)
        |
        +-- deriveUiSchemaFromSchema
        +-- fillDefaults
```

```
RunsRoutes
  |
  +-- /runs            -> ExperimentsIndex (cards with stats)
  +-- /runs/:exp       -> RunsList (column picker + filter)
  +-- /runs/:exp/:run  -> RunDetail (tree view + open-in-mlflow + open-job)
```

---

## Section 1 — B1. Per-type Job Detail Summary tab

### 1.1 Dispatcher

`frontend/src/routes/_authed.jobs.$id.tsx`:

```tsx
export default function JobDetailPage() {
  const { id = "" } = useParams();
  const { data: job } = useJob(id);
  if (!job) return <p className="text-muted-foreground">Loading…</p>;

  return (
    <JobDetailShell job={job}>
      <Tabs defaultValue="summary">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="artifacts" disabled={!job.mlflow_run_id}>Artifacts</TabsTrigger>
          {job.mlflow_run_id && (
            <TabsTrigger value="mlflow" asChild>
              <Link to={`/runs/${job.mlflow_experiment_id}/${job.mlflow_run_id}`}>Open run ↗</Link>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="summary" className="space-y-4">
          {job.type === "train"    && <TrainSummary job={job} />}
          {job.type === "evaluate" && <EvaluateSummary job={job} />}
          {job.type === "predict"  && <PredictSummary job={job} />}
        </TabsContent>

        <TabsContent value="logs">
          <LogTail text={(logText as string) ?? ""} />
        </TabsContent>

        <TabsContent value="artifacts">
          {job.mlflow_run_id ? <ArtifactTree runId={job.mlflow_run_id} /> :
            <p className="text-muted-foreground">No MLflow run recorded.</p>}
        </TabsContent>
      </Tabs>
    </JobDetailShell>
  );
}
```

`<JobDetailShell>` wraps the title + status badge + Cancel/Clone buttons + Metadata card + final `<ResolvedConfigCard>` (common across all three types).

### 1.2 `<TrainSummary>`

Layout (top to bottom):

- **Final metrics**: `<MetricsTable metrics={summary_metrics.metrics} />` (no whitelist — all keys).
- **Per-class breakdown** (if `summary_metrics.per_class`): `<PerClassMetrics per_class={...} positive_class={...} />`.
- **Confusion matrix** (if present).
- **Live metrics chart** (existing `<JobMetricChart>`, conditional on `hasTimeSeries`).
- **Trained model** card: `<TrainedModelCard jobId={job.id} />` — links to `/models/{name}` and shows the registered ModelVersion. Backend lookup via `useModelVersionForJob`.

### 1.3 `<EvaluateSummary>`

- **Source model** card: `<SourceModelCard sourceModelVersionId={job.source_model_version_id} />` — links to the original train job, shows model name + version + stage.
- **Evaluation metrics**: `<MetricsTable />`.
- **Per-class breakdown** (if present).
- **Confusion matrix**.

No live metrics chart (evaluate is one-shot — no `step ≥ 1` series).
No trained model card (evaluate doesn't produce one).

### 1.4 `<PredictSummary>`

- **Source model** card.
- **Prediction summary** card: `<PredictionSummaryCard summary={summary_metrics.prediction_summary} />` — total samples, distribution table + bar chart (Malware vs Benign counts and percentages), duration_seconds.
- **Download `predictions.csv`** button — downloads via the existing `/api/v1/runs/{run_id}/artifacts/download?path=predictions.csv` endpoint.

No metrics card, no per-class, no confusion matrix (predict has no ground truth).

### 1.5 New components

#### `<MetricsTable metrics: Record<string, number>>`

Replaces the whitelist-filtering `<MetricCards>`:

- Show every numeric value in `metrics`.
- 2-column responsive grid of small cards, each with metric name (humanized: `roc_auc` → `ROC AUC`) and value formatted to 4 decimal places.
- Special-cased keys (configurable in component): `accuracy`, `precision`, `recall`, `f1` rendered first in that order; remaining keys alphabetical.
- Empty state: "No metrics recorded for this job."

#### `<PerClassMetrics per_class: Record<string, ClassMetric>, positive_class: string>`

```ts
interface ClassMetric { precision: number; recall: number; f1: number; support: number }
```

Render as a simple table with class name as row, metric as column, support count rightmost. Highlight the `positive_class` row.

#### `<SourceModelCard sourceModelVersionId: string>`

- Fetch `useModelVersion(id)`.
- Show: model name (linked to `/models/{name}`), version, current stage, original train job link (linked to `/jobs/{source_job_id}`).
- Loading state, missing state ("Source model not registered" — should not happen for evaluate/predict since backend validates, but guard anyway).

#### `<TrainedModelCard jobId: string>`

- Fetch model versions, filter where `source_job_id == jobId`.
- Show one card per registered model version (usually 1; if 0, "Model not yet registered (training succeeded but registration failed — see backend logs)").

#### `<PredictionSummaryCard summary: PredictionSummary>`

```ts
interface PredictionSummary {
  total: number;
  distribution: Record<string, number>;  // class name → count
  duration_seconds: number;
}
```

Layout: total + duration on top row; horizontal stacked bar chart of class distribution; table below with class, count, percentage.

### 1.6 Backend — projection changes

#### 1.6a `summary_metrics.per_class`

`_project_summary_metrics` extends to handle a new `JobEvent.kind == "per_class"`:

```python
elif kind == "per_class":
    per_class = payload.get("per_class")
```

The maldet evaluator emit (external dep) becomes:

```python
# in maldet/src/maldet/evaluators/binary.py
logger.log_event("per_class", per_class=per_class)
```

If maldet is not yet updated, `per_class` stays `None` and `<PerClassMetrics>` is hidden — graceful degradation, not a hard error.

#### 1.6b `summary_metrics.prediction_summary`

`_handle_job_succeeded` adds for predict jobs:

```python
if j.type == JobType.PREDICT:
    try:
        await _project_prediction_summary(session, j)
    except Exception:
        BACKEND_ERRORS.labels(stage="prediction_summary_projection").inc()
        logger.exception("prediction_summary projection failed", extra={"job_id": str(j.id)})
```

Implementation:

```python
async def _project_prediction_summary(session: AsyncSession, j: Job) -> None:
    """Read predictions.csv via MLflow artifacts, compute distribution, store in summary_metrics."""
    # Fetch via MLflow client (we already have it in scope from caller)
    artifact_uri = ...  # mlflow-artifacts:/...
    csv_text = await _read_mlflow_artifact(j.mlflow_run_id, "predictions.csv")
    df = pandas.read_csv(io.StringIO(csv_text))
    distribution = df["predicted_class"].value_counts().to_dict()
    total = int(len(df))
    duration_seconds = (j.finished_at - j.started_at).total_seconds() if j.started_at else None

    sm = j.summary_metrics or {}
    sm["prediction_summary"] = {
        "total": total,
        "distribution": distribution,
        "duration_seconds": duration_seconds,
    }
    j.summary_metrics = sm
    flag_modified(j, "summary_metrics")  # SQLAlchemy JSONB mutation
    await session.commit()
```

`pandas` is already a transitive dep of mlflow; no new dep.

### 1.7 New endpoint (optional fallback)

`GET /api/v1/jobs/{job_id}/prediction-summary`:

```python
@router.get("/{job_id}/prediction-summary")
async def get_prediction_summary(job: Job = Depends(...)):
    if (job.summary_metrics or {}).get("prediction_summary"):
        return job.summary_metrics["prediction_summary"]
    # Cache miss (legacy job before this projection landed) — recompute
    return await _project_prediction_summary_one_shot(job)
```

Front-end uses this only for legacy jobs that don't have the cached field.

### 1.8 Files touched

- `frontend/src/routes/_authed.jobs.$id.tsx` (rewrite)
- `frontend/src/components/jobs/JobDetailShell.tsx` (new)
- `frontend/src/components/jobs/TrainSummary.tsx`, `EvaluateSummary.tsx`, `PredictSummary.tsx` (new)
- `frontend/src/components/jobs/MetricsTable.tsx` (new — replaces `MetricCards.tsx`)
- `frontend/src/components/jobs/PerClassMetrics.tsx`, `SourceModelCard.tsx`, `TrainedModelCard.tsx`, `PredictionSummaryCard.tsx` (new)
- `frontend/src/components/charts/MetricCards.tsx` — deleted
- `frontend/src/api/queries/models.ts` — add `useModelVersion(id)`, `useModelVersionForJob(jobId)`
- `backend/app/reconciler.py` — extend `_project_summary_metrics` for `per_class`; add `_project_prediction_summary`
- `backend/app/routers/jobs.py` — new `GET /jobs/{id}/prediction-summary` fallback endpoint
- maldet `src/maldet/evaluators/binary.py` — emit `per_class` event (external dep)

---

## Section 2 — B2. Submit form Hyperparameters polish

### 2.1 `RjsfConfigForm` enhancements

`frontend/src/components/forms/RjsfConfigForm.tsx`:

```tsx
import Form from "@rjsf/core";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";

interface Props {
  schema: object;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
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

  // Pre-populate defaults whenever schema changes (stage switch).
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

### 2.2 `deriveUiSchemaFromSchema`

Walks the schema, builds a uiSchema:

```ts
function deriveUiSchemaFromSchema(schema: RJSFSchema): UiSchema {
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  walk(schema, ui, []);
  return ui;
}

function walk(node: any, ui: UiSchema, path: string[]) {
  if (!node || typeof node !== "object") return;
  if (node.properties) {
    for (const [k, child] of Object.entries(node.properties)) {
      const childUi: UiSchema = ui[k] ?? {};
      const c = child as any;
      if (typeof c.description === "string") {
        childUi["ui:help"] = c.description;
      }
      if (c.default !== undefined) {
        childUi["ui:placeholder"] = `Default: ${JSON.stringify(c.default)}`;
      }
      ui[k] = childUi;
      walk(c, childUi, [...path, k]);
    }
  }
}
```

### 2.3 `fillDefaults`

```ts
function fillDefaults(
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
    // Don't descend into nested objects' defaults — RJSF handles that internally
    // via formData.k = {…} once the user expands the section.
  }
  return out;
}
```

### 2.4 `<StageExplainer>`

`frontend/src/components/forms/StageExplainer.tsx`:

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
        <p className="text-muted-foreground">{t(`stage.${type}.description`)}</p>
        <div className="flex flex-wrap gap-2 pt-2">
          {REQUIRED_FIELDS[type].map((f) => (
            <Badge key={f} variant="default">{t(`stage.field.${f}`)} (required)</Badge>
          ))}
          {OPTIONAL_FIELDS[type].map((f) => (
            <Badge key={f} variant="outline">{t(`stage.field.${f}`)} (optional)</Badge>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
```

i18n keys (`frontend/src/i18n/zh-TW.json` — currently empty `{}`, populate now):

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
  "stage.field.hyperparameters": "Hyperparameters"
}
```

`en.json` gets parallel English strings; framework already wired.

### 2.5 `<JobSubmitForm>` integration

```tsx
<Card>
  <CardHeader><CardTitle>Job type</CardTitle></CardHeader>
  <CardContent>...</CardContent>
</Card>

<StageExplainer type={type} />     {/* new, between Job type and Detector */}

<Card>
  <CardHeader><CardTitle>Detector</CardTitle></CardHeader>
  ...
```

### 2.6 Tests

- vitest unit `RjsfConfigForm.logic.test.ts`:
  - `deriveUiSchemaFromSchema` — properties with description/default/title produce the right uiSchema.
  - `fillDefaults` — defaults applied; existing values not overwritten; nullable union not overwritten by null default.
- vitest integration `RjsfConfigForm.test.tsx`:
  - render with elfrfdet `TrainConfig` schema, verify placeholder shows `Default: 100`, ui:help description visible, formData populated with defaults.
  - switch schema (simulate stage switch) — formData resets to new schema's defaults.
- playwright `submit.spec.ts`:
  - visit `/jobs/new`, click `Train`, verify StageExplainer shows train description and field badges, hyperparameter form shows defaults, descriptions visible.
  - same for `Evaluate`, `Predict`.

### 2.7 Files touched

- `frontend/src/components/forms/RjsfConfigForm.tsx` (rewrite)
- `frontend/src/components/forms/StageExplainer.tsx` (new)
- `frontend/src/components/forms/JobSubmitForm.tsx` (insert `<StageExplainer>`)
- `frontend/src/i18n/zh-TW.json`, `en.json` (populate stage keys)
- `frontend/tests/unit/RjsfConfigForm.logic.test.ts` (new)
- `frontend/tests/unit/RjsfConfigForm.test.tsx` (new)
- `frontend/tests/e2e/submit.spec.ts`

---

## Section 3 — B3. Resolved config / Run detail tree viewer

### 3.1 New `<JsonTreeView>` (generic)

`frontend/src/components/common/JsonTreeView.tsx`:

```tsx
import ReactJsonView from "react-json-view";

interface Props {
  value: unknown;
  collapsed?: number | boolean;   // depth at which to collapse, or fully collapse
  copyable?: boolean;
}

export function JsonTreeView({ value, collapsed = 1, copyable = true }: Props) {
  return (
    <div className="overflow-auto rounded-md border">
      <ReactJsonView
        src={value as object}
        name={false}
        collapsed={collapsed}
        displayDataTypes={false}
        displayObjectSize={false}
        enableClipboard={copyable}
        theme="rjv-default"  // light theme, paired with our card bg
        style={{ padding: "0.75rem", fontSize: "0.8rem", fontFamily: "ui-monospace, monospace" }}
      />
    </div>
  );
}
```

Used in 3 places:
- `<ResolvedConfigCard>` (job detail).
- Detector manifest `<ManifestView>` (replaces `<JsonViewer value={manifest} />`).
- `<RunDetail>` for params and tags (replaces `<JsonViewer value={run.params} />`).

### 3.2 `<ResolvedConfigCard>` (B3-specific)

`frontend/src/components/jobs/ResolvedConfigCard.tsx`:

```tsx
interface Props {
  resolvedConfig: Record<string, unknown>;
  userParams: Record<string, unknown> | null;   // null for legacy jobs
  detectorDefaults: Record<string, unknown> | null;  // optional, for default-vs-overridden indicator
}

export function ResolvedConfigCard({ resolvedConfig, userParams, detectorDefaults }: Props) {
  const [expanded, setExpanded] = useState(false);
  const lineCount = JSON.stringify(resolvedConfig, null, 2).split("\n").length;

  return (
    <Card>
      <CardHeader><CardTitle>Resolved config</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {userParams !== null ? (
          <UserParamsTable userParams={userParams} defaults={detectorDefaults} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Legacy job — user-supplied params not recorded.
          </p>
        )}
        <button
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
          onClick={() => setExpanded((x) => !x)}
        >
          {expanded ? "▼" : "▶"} {expanded ? "Hide" : "Show"} full resolved config ({lineCount} lines)
        </button>
        {expanded && <JsonTreeView value={resolvedConfig} collapsed={1} />}
      </CardContent>
    </Card>
  );
}
```

`<UserParamsTable>`: shows each `userParams[k]` as a row with the value; if `detectorDefaults` is provided, marks rows where `userParams[k] === detectorDefaults[k]` as "(default)" in muted text and highlights overrides in bold.

### 3.3 Backend — `Job.user_params`

#### Model and migration

`backend/app/models/job.py`:

```python
user_params: Mapped[dict | None] = mapped_column(
    JSONB, nullable=True,
    comment="Raw user-submitted params before defaults merge — needed for resolved-config UI.",
)
```

Migration:

```python
def upgrade():
    op.add_column("job", sa.Column("user_params", postgresql.JSONB, nullable=True))

def downgrade():
    op.drop_column("job", "user_params")
```

Existing jobs get NULL — `<ResolvedConfigCard>` shows the legacy-job fallback.

#### Endpoint

`backend/app/routers/jobs.py:submit_job`:

```python
job = Job(
    ...,
    user_params=body.params,    # raw user input, before defaults merge
    resolved_config=...,
)
```

`JobRead` schema gains `user_params: dict | None`.

Detector defaults retrieval: from the manifest `params_schema` `default` fields. Backend can compute and return these in the same `JobRead` response (`detector_defaults: dict | None`) by extracting from the version's `manifest.stages.<type>.params_schema`. Optional — if too costly, frontend can re-derive from `useDetectorVersion` separately. Plan picks the simpler option after measuring.

### 3.4 Tests

- vitest `<ResolvedConfigCard>`:
  - userParams provided + defaults → table rows with override indicators.
  - userParams null → legacy fallback message.
  - expand → JsonTreeView visible.
- pytest `submit_job_records_user_params` — POST with params, GET /jobs/{id}, assert `user_params` returned.

### 3.5 Files touched

- `frontend/src/components/common/JsonTreeView.tsx` (new)
- `frontend/src/components/common/JsonViewer.tsx` (deleted)
- `frontend/src/components/jobs/ResolvedConfigCard.tsx` (new)
- `frontend/src/components/jobs/UserParamsTable.tsx` (new)
- `frontend/src/routes/_authed.detectors.$id.tsx` — replace `<JsonViewer>` with `<JsonTreeView>` in `<ManifestView>`
- `frontend/src/routes/_authed.runs.$expId.$runId.tsx` — replace JsonViewer for params/tags
- `backend/app/models/job.py` — add `user_params` column
- `backend/app/schemas/job.py` — add to `JobRead`
- `backend/app/routers/jobs.py` — write `user_params` on submit
- `backend/migrations/versions/<hash>_phase13b_user_params.py` (new)
- `frontend/package.json` — add `react-json-view`

---

## Section 4 — B4. Runs three-tier light overhaul

### 4.1 `/runs` (experiments index)

#### Frontend

`frontend/src/routes/_authed.runs._index.tsx` (rewrite):

```tsx
export default function ExperimentsListPage() {
  const { data, isLoading } = useExperimentsWithStats();
  if (isLoading) return <p>Loading…</p>;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Experiments</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(data ?? []).map((exp) => <ExperimentCard key={exp.experiment_id} exp={exp} />)}
      </div>
    </div>
  );
}
```

`<ExperimentCard>`:

```
┌─────────────────────────────────────┐
│ #2  train_runs                      │
│ 23 runs  ·  Best F1: 0.9134         │
│ Last run: 3h ago                    │
│ ──────────────────────────────────  │
│ Open runs →           ↗ MLflow      │
└─────────────────────────────────────┘
```

#### Backend — aggregate endpoint

`backend/app/routers/experiments_proxy.py`:

> **Note**: `cachetools.@cached` does not support `async` functions — it would cache the coroutine object, not the awaited result. The implementation uses a manual cache lookup pattern (consistent with industry standard for async TTL caching without extra deps; `asyncache` package would be an alternative but adds a dependency for one site).

```python
from cachetools import TTLCache
import asyncio

_stats_cache: TTLCache[str, dict] = TTLCache(maxsize=64, ttl=30)  # 30s TTL, human-paced UI
_stats_locks: dict[str, asyncio.Lock] = {}  # avoid stampede on cold cache

@router.get("/experiments")
async def list_experiments(
    user: User = Depends(current_active_user),
    max_results: int = Query(100, ge=1, le=1000),
    include: str | None = Query(None, regex="^stats$"),
):
    experiments = await _client().search_experiments(max_results=max_results)
    if include != "stats":
        return experiments

    enriched = []
    for exp in experiments:
        stats = await _experiment_stats(exp["experiment_id"])
        enriched.append({**exp, **stats})
    return enriched

async def _experiment_stats(experiment_id: str) -> dict:
    if experiment_id in _stats_cache:
        return _stats_cache[experiment_id]
    lock = _stats_locks.setdefault(experiment_id, asyncio.Lock())
    async with lock:
        if experiment_id in _stats_cache:        # double-check after lock
            return _stats_cache[experiment_id]
        runs = await _client().search_runs([experiment_id], max_results=1000)
        finished = [r for r in runs if r.get("status") == "FINISHED"]
        f1s = [r.get("metrics", {}).get("f1") for r in finished]
        result = {
            "run_count": len(runs),
            "best_f1": max([x for x in f1s if x is not None], default=None),
            "latest_start_time": max(
                [r["start_time"] for r in runs if r.get("start_time")], default=None,
            ),
        }
        _stats_cache[experiment_id] = result
        return result
```

### 4.2 `/runs/:expId` (runs list)

#### Column picker

`<RunsColumnPicker>` shadcn dropdown-checkbox inside a button. Reads available metric names + param names from the runs payload. Persists per-experiment selection to `localStorage` key `runs.columns.{expId}`.

#### Default columns

`["run_id", "status", "duration", "metrics.f1", "metrics.accuracy", "lolday_job"]`.

#### Status filter

shadcn Select with options `all | FINISHED | FAILED | RUNNING | SCHEDULED`. Persists to `localStorage` key `runs.status.{expId}`.

#### "↗ Open in MLflow" button

Top-right of the runs list page. Links to `/mlflow/#/experiments/{experiment_id}` (or compare URL if multiple rows selected — defer multi-select to a later phase, not in 13b).

### 4.3 `/runs/:expId/:runId` (run detail)

```tsx
export default function RunDetailPage() {
  const { expId = "", runId = "" } = useParams();
  const { data } = useRun(runId);
  const { data: cm } = useConfusionMatrix(runId);
  if (!data) return <p>Loading…</p>;
  const run = data as RunData;
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
        <CardHeader><CardTitle>Metrics</CardTitle></CardHeader>
        <CardContent><MetricsTable metrics={run.metrics ?? {}} /></CardContent>
      </Card>
      {cm && (
        <Card>
          <CardHeader><CardTitle>Confusion matrix</CardTitle></CardHeader>
          <CardContent><ConfusionMatrix labels={cm.labels} matrix={cm.matrix} /></CardContent>
        </Card>
      )}
      <CollapsibleCard title="Parameters">
        <JsonTreeView value={run.params ?? {}} />
      </CollapsibleCard>
      <CollapsibleCard title="Tags">
        <JsonTreeView value={run.tags ?? {}} />
      </CollapsibleCard>
      <Card>
        <CardHeader><CardTitle>Artifacts</CardTitle></CardHeader>
        <CardContent><ArtifactTree runId={runId} /></CardContent>
      </Card>
    </div>
  );
}
```

`<OpenInMlflowButton>`, `<OpenInLoldayJobButton>` — small icon-link buttons.

### 4.4 New components

| Component | File | Purpose |
|---|---|---|
| `<ExperimentCard>` | `frontend/src/components/runs/ExperimentCard.tsx` | Cards on `/runs` |
| `<RunsColumnPicker>` | `frontend/src/components/runs/RunsColumnPicker.tsx` | Toggle metrics/params columns |
| `<RunsStatusFilter>` | `frontend/src/components/runs/RunsStatusFilter.tsx` | Status dropdown |
| `<OpenInMlflowButton>` | `frontend/src/components/common/OpenInMlflowButton.tsx` | Generic deep-link |
| `<OpenInLoldayJobButton>` | `frontend/src/components/common/OpenInLoldayJobButton.tsx` | Run → job link |
| `<CollapsibleCard>` | `frontend/src/components/common/CollapsibleCard.tsx` | Card with collapse toggle (used for Params, Tags) |

### 4.5 Tests

#### Backend (pytest)

- `test_experiments_with_stats_aggregates_runs` — mock MLflow client, verify enriched payload.
- `test_experiments_stats_cached_30s` — two consecutive calls hit MLflow once.
- `test_experiments_no_include_returns_bare_list` — no stats when `include` not specified.

#### Frontend

- vitest `<RunsColumnPicker>`:
  - reads metric/param keys from data, renders checkboxes.
  - persists selection to localStorage.
  - column visibility updates DataTable.
- playwright:
  - visit `/runs`, verify ExperimentCard shows run count and best F1.
  - visit `/runs/:exp`, open column picker, toggle, verify column appears/disappears.
  - visit `/runs/:exp/:run`, click "Open job" → goes to `/jobs/<id>`.

### 4.6 Files touched

- `frontend/src/routes/_authed.runs._index.tsx` (rewrite)
- `frontend/src/routes/_authed.runs.$expId.tsx` (rewrite)
- `frontend/src/routes/_authed.runs.$expId.$runId.tsx` (rewrite)
- `frontend/src/api/queries/runs.ts` — `useExperimentsWithStats` (passes `include=stats`)
- `frontend/src/components/runs/*` (new files)
- `frontend/src/components/common/OpenInMlflowButton.tsx`, `OpenInLoldayJobButton.tsx`, `CollapsibleCard.tsx`
- `backend/app/routers/experiments_proxy.py` — add `include=stats` aggregate path

---

## Section 5 — B5. Expose MLflow UI

### 5.1 IngressRoute change

`charts/lolday/templates/ingress.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: lolday
  namespace: {{ .Release.Namespace }}
spec:
  entryPoints: [web]
  routes:
    # Backend API — highest priority
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/api/v1`)
      priority: 10
      services:
        - kind: Service
          name: backend
          port: 8000

    # MLflow UI — read-only (GET / HEAD / OPTIONS only)
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
          port: {{ .Values.mlflow.service.port }}

    # MLflow non-GET — block with 405
    - kind: Rule
      match: Host(`{{ .Values.frontend.host }}`) && PathPrefix(`/mlflow`)
      priority: 5
      middlewares:
        - name: mlflow-deny-write
      services:
        - kind: Service
          name: backend  # required field; service is unreachable due to middleware
          port: 8000

    # Frontend catch-all
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
  namespace: {{ .Release.Namespace }}
spec:
  stripPrefix:
    prefixes:
      - /mlflow
---
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: mlflow-deny-write
  namespace: {{ .Release.Namespace }}
spec:
  errors:
    status: ["200"]    # always-true match
    service:
      name: traefik-default-error
      port: 80
    query: "/{status}.html"
```

> Note: Traefik does not have a built-in "respond with 405" primitive. The cleanest implementation uses an upstream `errors` middleware pointing at a static-response service. An alternative is `forwardAuth` to a tiny lambda that returns 405. The plan picks the simplest option that works in the lab's Traefik build (`errors` middleware to a service responding 405; if not feasible, fallback to a 1-line nginx Pod serving 405 for all routes).

### 5.2 MLflow `--static-prefix`

`charts/lolday/templates/mlflow.yaml`:

```yaml
args:
  - --host=0.0.0.0
  - --port={{ .Values.mlflow.service.port }}
  - --backend-store-uri=postgresql+psycopg2://...
  - --default-artifact-root=mlflow-artifacts:/
  - --artifacts-destination=/mlflow-artifacts
  - --serve-artifacts
  - --static-prefix=/mlflow      # new
```

This makes MLflow rewrite all asset URLs in HTML to `/mlflow/static-files/...`, matching what the Traefik `stripPrefix` middleware will preserve from the client request.

### 5.3 Cloudflare Access

Existing CFA application targets the entire host `lolday.<domain>`. `/mlflow/` inherits the same policy automatically — no CFA configuration change.

Verification: an unauthenticated browser hitting `https://lolday.<domain>/mlflow/` should be redirected to CFA login, not see MLflow.

### 5.4 Verification (post-deploy)

- **HTML asset paths**: `curl -s https://lolday/<domain>/mlflow/ | grep -o '/static-files/[^"]*'` should be empty (all should be `/mlflow/static-files/...`).
- **Read works**: browser visit `/mlflow/` → MLflow experiments page renders.
- **Write blocked**: `curl -X POST https://lolday/<domain>/mlflow/api/2.0/mlflow/runs/create -d '{}'` → 405.
- **CFA gate**: visit in incognito → CFA login page.

### 5.5 SSH safety

This change touches only Traefik IngressRoute (K3s app layer) and MLflow Pod args. No host-level firewall, iptables, or sysctl changes. Per CLAUDE.md SSH protection: **no SSH risk**.

### 5.6 Files touched

- `charts/lolday/templates/ingress.yaml`
- `charts/lolday/templates/mlflow.yaml`

---

## Migration & Deploy

### Order

1. **Migration** — `Job.user_params` column add (independent, must run before backend with new schema).
2. **Backend deploy** — reconciler projection extensions (`per_class`, `prediction_summary`), `submit_job` writes `user_params`, experiments aggregate endpoint.
3. **Chart deploy** — MLflow `--static-prefix` + new IngressRoute. MLflow Pod restarts (one-time; reads from same DB).
4. **Frontend deploy** — all UX components.
5. **maldet release** (external, not tracked here): emit `per_class` event for evaluators. After this lands and detectors rebuild, `<PerClassMetrics>` becomes populated; until then, gracefully hidden.

### Rollback

- Migration: forward-only column add; rollback would `DROP COLUMN job.user_params`. Safe if frontend deployed back too (frontend tolerates `user_params=null`).
- Backend: revert. Reconciler projection extensions are additive — DB still readable by old backend.
- Chart: `helm rollback`. MLflow Pod restarts back to no static-prefix; frontend MLflow links would 404 until frontend also rolled back. Plan deploys chart and frontend together.
- Frontend: revert bundle.

### Per-class projection cold start

After backend deploy, only **new** evaluate jobs get `per_class` populated. Existing finished jobs would not have it (events are still in `job_event` table; can be backfilled via a one-shot script if useful, otherwise legacy view shows "no per-class breakdown available").

### Prediction summary cold start

Same pattern. New predict jobs get cached `prediction_summary` in `summary_metrics`. Legacy predict jobs would hit the fallback endpoint, which recomputes from `predictions.csv` artifact on demand.

---

## Testing strategy

### Unit / integration

| Layer | Test |
|---|---|
| Backend | `_project_summary_metrics` extended for `per_class` event |
| Backend | `_project_prediction_summary` reads CSV, computes distribution, caches |
| Backend | `submit_job` writes `user_params` |
| Backend | `experiments?include=stats` endpoint, with cache |
| Frontend | `deriveUiSchemaFromSchema`, `fillDefaults` |
| Frontend | `<MetricsTable>` shows all keys (no whitelist) |
| Frontend | `<TrainSummary>` / `<EvaluateSummary>` / `<PredictSummary>` render correct cards per type |
| Frontend | `<ResolvedConfigCard>` user-params table + collapsed full config |
| Frontend | `<JsonTreeView>` renders tree, copy works |
| Frontend | `<RunsColumnPicker>` localStorage persistence |
| Frontend | `<ExperimentCard>` shows run_count / best_f1 |

### E2E (playwright)

- Submit train → detail shows TrainSummary + trained model card.
- Submit evaluate → detail shows EvaluateSummary + source model card + per-class.
- Submit predict → detail shows PredictSummary + distribution + download button.
- Submit form: switch type, defaults pre-populate, descriptions visible.
- Resolved config: user params shown, expand → tree visible.
- Runs index: ExperimentCard with stats; click Open in MLflow → `/mlflow/`.
- Run detail: tree view for params/tags; Open job → lolday job page.
- MLflow UI loads at `/mlflow/`, asset URLs include prefix, POST blocked.

### Manual verification

1. Visit a recent train job — TrainedModelCard links to model registry.
2. Visit a recent evaluate job — per-class table populated (after maldet release lands).
3. Visit a recent predict job — distribution + percentages + download CSV works.
4. Submit form: pick `Train`, see Chinese stage explainer, pre-populated `n_estimators=100`, description "Number of trees in the forest." visible.
5. Click "Open in MLflow" from any of the 3 runs tiers — opens `/mlflow/...` correctly.
6. From an MLflow run page, no `Edit` button works (read-only enforced).
7. Run detail "↗ Open job" goes back to the lolday job that produced this run.

---

## Open Questions

1. **Detector defaults source for `<UserParamsTable>` override indicator** — fetch from manifest endpoint client-side (cleaner, costs one query per job detail load) or include in `JobRead` response (denormalized, slight write-side cost on submit). Plan picks after measuring page-load timing.
2. **maldet `per_class` event emit** — depends on a maldet PR. If timing slips, lolday Phase 13b ships with `<PerClassMetrics>` rendered from raw event data only when present, the maldet release follows independently.
3. **Multi-select runs compare** — explicitly out of scope (Q6 chose to delegate to MLflow UI). May revisit later if MLflow's compare UX proves insufficient for the lab's HP tuning workflow.
4. **MLflow write-block implementation** — Traefik does not have a single primitive for "405 on non-GET." Plan tries `errors` middleware first; falls back to a tiny static-response Pod if needed. If neither is feasible in the lab's Traefik build, default to option B from Section 5 brainstorm (no method restriction; CFA + lab trust as the boundary).

---

## Appendix A — Sample `summary_metrics` shape (Phase 13b)

```jsonc
// Train job
{
  "metrics": {
    "accuracy": 0.94, "precision": 0.92, "recall": 0.93, "f1": 0.92, "roc_auc": 0.97
  },
  "confusion_matrix": { "labels": ["Benign", "Malware"], "matrix": [[450, 20], [25, 505]] },
  "per_class": {
    "Malware": { "precision": 0.96, "recall": 0.95, "f1": 0.95, "support": 530 },
    "Benign":  { "precision": 0.90, "recall": 0.91, "f1": 0.90, "support": 470 }
  }
}

// Predict job
{
  "metrics": {},
  "confusion_matrix": null,
  "prediction_summary": {
    "total": 1024,
    "distribution": { "Malware": 612, "Benign": 412 },
    "duration_seconds": 12.3
  }
}
```

---

## Appendix B — `<JsonTreeView>` usage map

| Caller | `value` | `collapsed` | Where |
|---|---|---|---|
| `<ManifestView>` | manifest dict | 1 | Detector versions tab |
| `<ResolvedConfigCard>` | `job.resolved_config` | 1 | Job detail Summary tab |
| `<CollapsibleCard>` for Params | `run.params` | (controlled by parent) | Run detail |
| `<CollapsibleCard>` for Tags | `run.tags` | (controlled) | Run detail |
