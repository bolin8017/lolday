# maldet 2.0 + Lolday Runs UX cleanup — design

Date: 2026-05-01
Status: Draft (awaiting plan)

## 1. Context

Four user-reported issues converge on the same code path (the train / evaluate / predict ML lifecycle and its UI surfacing in Lolday):

1. **Malware label encoded as 0 instead of 1** in some surfaces. The user expects positive class = 1 by sklearn / industry convention.
2. **Old run logs show `(no output)`**. Logs predate the current `events.jsonl` capture pipeline.
3. **Artifact download default filename is `download`**, because the backend sends no `Content-Disposition` and the frontend's `<a download>` falls back to the URL basename.
4. **Job Detail's "Open run ↗" tab leads to `/runs/:expId/:runId` which is confusing**: it shows raw MLflow primitives, predict runs have empty Parameters, and the link to MLflow native UI is unfriendly to non-engineers.

Investigation revealed several **root causes that are deeper than the user-reported symptoms** and warrant a single coordinated fix:

- **maldet (the user-authored ML framework, `islab-malware-detector`) has internal label-encoding inconsistency**: `sklearn_trainer.py` and `lightning_trainer.py` hard-code `1 if "Malware" else 0` (sklearn alphabetical convention), `runner.py` picks `positive_class = classes[0]` (positive-first convention), and `predictors.py` decodes via `class_names[int(p)]` (depends on `classes` ordering). Whichever ordering the manifest author picks, **at least one of these three layers disagrees**, producing inverted predictions or an inverted positive-class assignment for metrics.
- **maldet's `BinaryClassification.evaluate` writes `confusion_matrix.labels` in `[positive, other]` order while the matrix itself is computed with `confusion_matrix(y, y_pred, labels=[0, 1])` — so labels metadata never matches matrix orientation**. Frontend always renders an inverted CM.
- **maldet's evaluator never emits `confusion_matrix` or `per_class` events**, so Lolday's reconciler `_project_summary_metrics` (which expects those event kinds) **never populates `Job.summary_metrics.confusion_matrix` or `.per_class` in production**. The Job Detail Per-class metrics card and Confusion matrix card are dead UI today.
- **Lolday's `/runs/:expId/:runId` page is structurally redundant** with Job Detail (Phase 13b B1 made Job Detail the canonical typed view). Predict runs additionally have nothing meaningful to show under Parameters because `BatchPredictor` has no params to log.

The user has authorised:

- Cross-repo work (maldet + lolday)
- Breaking schema changes
- A maintenance window with full data wipe (no historical runs preserved)
- One-shot rollout (no canary detector)

## 2. Goals / Non-goals

### Goals

- Make `Malware = 1` (positive class) **structurally invariant** across the entire pipeline (manifest → trainer → predictor → evaluator → metrics → frontend), declared **explicitly** in the manifest.
- Ensure the existing Job Detail Per-class metrics and Confusion matrix cards actually render in production.
- Fix the artifact download filename to be the artifact's basename.
- Remove the redundant `/runs/:expId/:runId` page; canonicalise on Job Detail.
- Verify lazy-create of MLflow experiment after wiping all experiment shells.

### Non-goals

- No rewrite of the Job submission UX, Job Detail summary cards, Runs list filtering, or MLflow integration semantics beyond what's needed for the four issues.
- No backwards compatibility for maldet 1.x manifests, model artifacts, MLflow runs, or Lolday Job rows. All pre-cutover history is wiped.
- No new MLflow features (e.g., system metrics, code-version capture). Current event stream is the only metric channel.

## 3. Architecture overview

### 3.1 Repos in scope

```
islab-malware-detector (maldet, user-authored)
  ├── 1.x → 2.0 major bump (PyPI release)
  └── changes: OutputConfig schema, trainers, predictor, evaluator, EventKind enum

lolday (this repo)
  ├── backend: bump maldet→>=2.0,<3, fix download endpoint, update test fixtures
  ├── frontend: delete Run Detail page, Job Detail gains Open in MLflow, ArtifactTree adds download attr
  └── build-helper image: bump maldet→>=2.0,<3, rebuild

ops (server30 + each detector repo)
  ├── every detector repo: add positive_class to maldet.toml + bump maldet
  ├── all detector images rebuilt
  └── baseline train + evaluate + predict re-run after data wipe
```

### 3.2 Data flow after fix

```
detector container (maldet 2.0)
  ↓ events.jsonl
event-tailer sidecar (job_helper.tail_events)
  ↓ POST → backend internal events endpoint
JobEvent table  (now includes confusion_matrix + per_class kinds emitted by evaluator)
  ↓
reconciler._project_summary_metrics  (existing code, unchanged)
  ↓
Job.summary_metrics = { metrics, confusion_matrix, per_class, prediction_summary }
  ↓
Frontend Job Detail (TrainSummary / EvaluateSummary / PredictSummary, existing components)
  ↓
Cards now actually render in production.
```

### 3.3 Breaking-change inventory

1. **maldet `OutputConfig.positive_class`** is required for `binary_classification`. Build-helper validator fails fast on missing.
2. **maldet `CompatConfig.schema_version: 2`**. Loading a `schema_version=1` manifest is rejected.
3. **maldet trainers no longer hard-code `Malware`**; encoding is `classes.index(label)`. Any saved sklearn / lightning model from maldet 1.x loaded by maldet 2.0 trainer's protocol gives inverted predictions if the manifest's `classes` ordering differs from the encoding the model was trained on. **Therefore all models must be retrained.**
4. **maldet `Trainer.fit` protocol** gains a required `classes: Sequence[str]` keyword arg. Custom Trainer subclasses authored by detector authors must update their signature.
5. **Lolday `/runs/:expId/:runId` route** is replaced with redirect logic. Old deeplinks continue to work (auto-jump to Job Detail or MLflow).

## 4. maldet 2.0 (upstream library) design

### 4.1 Manifest schema

```python
# maldet/manifest.py

class OutputConfig(_Frozen):
    task: Literal["binary_classification", "multiclass_classification", "regression", "ranking"]
    classes: list[str] = Field(default_factory=list)
    positive_class: str | None = None
    score_range: tuple[float, float] = (0.0, 1.0)

    @model_validator(mode="after")
    def _validate_positive_class(self) -> Self:
        if self.task == "binary_classification":
            if self.positive_class is None:
                raise ValueError(
                    "output.positive_class is required for binary_classification"
                )
            if self.positive_class not in self.classes:
                raise ValueError(
                    f"output.positive_class={self.positive_class!r} "
                    f"not in output.classes={self.classes!r}"
                )
            if len(self.classes) != 2:
                raise ValueError(
                    f"binary_classification requires exactly 2 classes, "
                    f"got {len(self.classes)}"
                )
        return self


class CompatConfig(_Frozen):
    schema_version: int = 2
    min_python: str = "3.12"
    min_maldet: str = "2.0"
```

`positive_class` is ignored for non-binary tasks (forward-compat hook for future multiclass extension via `target_classes: list[str]`).

### 4.2 Trainer

`SklearnTrainer._materialize` and `LightningTrainer._materialize_tensor` switch from hard-coded `1 if "Malware" else 0` to `classes.index(sample.label)`. They take `classes: Sequence[str]` from the runner via the new `Trainer.fit(..., classes=...)` kwarg.

Encoding now satisfies: `internal_label = classes.index(sample.label)`. Malware's internal label is whichever index the manifest places it at — typically 1 if `classes = ["Benign", "Malware"]` (alphabetical), but the system no longer assumes any particular ordering.

`maldet/protocols.py::Trainer.fit` signature changes:

```python
def fit(
    self, model, train, extractor, *,
    classes: Sequence[str],         # NEW
    val: SampleReader | None = None,
    logger: EventLogger,
) -> TrainResult: ...
```

### 4.3 Runner

`runner.py`:

```python
if stage == "train":
    result = trainer.fit(
        model, reader, extractor,
        classes=self._manifest.output.classes,
        logger=logger,
    )
elif stage == "evaluate":
    evaluator = evaluator_cls(
        positive_class=self._manifest.output.positive_class,
        class_names=self._manifest.output.classes,
    )
elif stage == "predict":
    predictor = predictor_cls(class_names=self._manifest.output.classes)
```

The "convention: classes[0] is positive" comment and behaviour are removed. `positive_class` is now an explicit manifest field.

### 4.4 Evaluator

`maldet/evaluators/binary.py::BinaryClassification.evaluate`:

```python
class_to_idx = {c: i for i, c in enumerate(self._classes)}
ys = [class_to_idx[s.label] for s in samples]
pos_idx = class_to_idx[self._positive]
y = np.asarray(ys)
y_pred = np.asarray(model.predict(features))

metrics = {
    "accuracy": float(accuracy_score(y, y_pred)),
    "precision": float(precision_score(y, y_pred, pos_label=pos_idx, zero_division=0)),
    "recall":    float(recall_score(   y, y_pred, pos_label=pos_idx, zero_division=0)),
    "f1":        float(f1_score(       y, y_pred, pos_label=pos_idx, zero_division=0)),
}

# Confusion matrix: labels and matrix orientation match by construction
labels_idx = list(range(len(self._classes)))
cm = confusion_matrix(y, y_pred, labels=labels_idx).tolist()
cm_payload = {"labels": list(self._classes), "matrix": cm}

# Per-class: indexed by class index, then mapped back to class name
p_per, r_per, f_per, s_per = precision_recall_fscore_support(
    y, y_pred, labels=labels_idx, zero_division=0
)
per_class = {
    self._classes[i]: {
        "precision": float(p_per[i]),
        "recall":    float(r_per[i]),
        "f1":        float(f_per[i]),
        "support":   int(s_per[i]),
    }
    for i in range(len(self._classes))
}

# Existing emit
for k, v in metrics.items():
    logger.log_metric(k, v)
# NEW: emit so reconciler projection sees them
logger.log_event("confusion_matrix", labels=cm_payload["labels"], matrix=cm_payload["matrix"])
logger.log_event("per_class", per_class=per_class)

return MetricReport(
    task="binary_classification",
    n_samples=len(y),
    duration_seconds=...,
    metrics=metrics,
    per_class=per_class,
    confusion_matrix=cm_payload,
)
```

Three coordinated fixes: (a) confusion matrix labels match matrix orientation, (b) metrics use `pos_label` explicitly, (c) `confusion_matrix` and `per_class` events are emitted.

### 4.5 Predictor

`builtins/predictors.py::BatchPredictor.predict` body unchanged. It still maps `pred_label = class_names[int(p)]`. The fix is that the trainer now encodes consistently, so this mapping is unconditionally correct.

### 4.6 EventKind enum

```python
# maldet/events/kinds.py
class EventKind(StrEnum):
    ...  # 9 existing kinds
    CONFUSION_MATRIX = "confusion_matrix"
    PER_CLASS = "per_class"

_REQUIRED_FIELDS[EventKind.CONFUSION_MATRIX] = ("labels", "matrix")
_REQUIRED_FIELDS[EventKind.PER_CLASS] = ("per_class",)
```

### 4.7 Versioning

- `maldet.__version__ = "2.0.0"` (major bump for breaking schema)
- `pyproject.toml` `requires-python = ">=3.12"`
- Migration guide in CHANGELOG: "manifest must add `output.positive_class`; rebuild required"
- Tag `v2.0.0` and PyPI publish via existing release flow

## 5. Lolday backend

### 5.1 Dependency bump

```toml
# backend/pyproject.toml
"maldet>=2.0,<3"

# charts/lolday/helpers/build-helper/pyproject.toml
"maldet[lightning]>=2.0,<3.0"
```

Rerun `uv lock` in both.

### 5.2 `download_artifact` Content-Disposition

`backend/app/routers/experiments_proxy.py::download_artifact` builds the header per RFC 6266:

```python
filename = PurePosixPath(path).name or "artifact"
ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii").replace('"', "_")
quoted = quote(filename, safe="")
content_disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'
media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
return Response(content=r.content, media_type=media_type, headers={"Content-Disposition": content_disposition})
```

Both `filename=` (ASCII fallback) and `filename*=UTF-8''…` (RFC 5987 percent-encoded) are sent. Browsers prefer `filename*` and fall back to `filename`. This handles non-ASCII artifact names (e.g. `混淆樣本.csv`) without breaking ASCII paths.

### 5.3 Reconciler projection

No code change. After the maldet 2.0 evaluator emits `confusion_matrix` and `per_class` events, `_project_summary_metrics` (`backend/app/reconciler/projections.py`) populates `Job.summary_metrics.confusion_matrix` and `.per_class` correctly. A Playwright e2e (§8) verifies end-to-end.

### 5.4 Test fixtures

Four locations have manifest fixtures missing `positive_class`:

- `backend/tests/conftest.py:428`
- `backend/tests/test_services_validator.py` (×4 occurrences)
- `backend/tests/test_services_validator_phase11b.py:24`
- `backend/tests/test_routers_jobs.py` (×2 occurrences)

Each gets `"positive_class": "Malware"` added. `classes` is also normalised to alphabetical `["Benign", "Malware"]` (cosmetic; behaviour decoupled from ordering).

### 5.5 Maintenance flag

New env var `BACKEND_MAINTENANCE_MODE` (bool). When true:

- `POST /api/v1/jobs` returns 503 with `Retry-After: 3600`
- Frontend detects 503 from job submit → friendly banner "Platform under maintenance"
- Read APIs (GET) remain available; reconciler still runs

The flag is set by operator before §7.5 cleanup and unset after §7.7 baseline acceptance. Rolled out as a one-line setting in `backend/app/config.py` plus a guard at the head of `routers/jobs.py::create_job`.

## 6. Lolday frontend

### 6.1 Run Detail page → redirect

`frontend/src/routes/_authed.runs.$expId.$runId.tsx` is replaced. New file:

```tsx
import { useParams, Navigate } from "react-router";
import { useRun } from "@/api/queries/runs";

export const handle = { breadcrumb: "Run" };

export default function RunRedirectPage() {
  const { expId = "", runId = "" } = useParams();
  const { data, isLoading, error } = useRun(runId);
  if (isLoading) return <p className="text-muted-foreground">Loading…</p>;
  if (error || !data) return <Navigate to="/runs" replace />;
  const run = data as { tags?: Record<string, string> };
  const jobId = run.tags?.["lolday.job_id"] ?? run.tags?.lolday_job_id;
  if (jobId) return <Navigate to={`/jobs/${jobId}`} replace />;
  window.location.replace(`/mlflow/#/experiments/${expId}/runs/${runId}`);
  return <p className="text-muted-foreground">Redirecting to MLflow…</p>;
}
```

The old `useConfusionMatrix` hook (which fetched `confusion_matrix.json` artifact that never existed) is deleted.

### 6.2 Job Detail

`frontend/src/routes/_authed.jobs.$id.tsx`:

- Remove the "Open run ↗" tab (the run is now the same conceptual object as the job).
- Tabs become: Summary, Logs, Artifacts.

`frontend/src/components/jobs/JobDetailShell.tsx`:

- Add `OpenInMlflowButton` to the header action bar when `job.mlflow_run_id` and `job.mlflow_experiment_id` are set.
- Visible to all authenticated users (CFA-protected MLflow at `/mlflow` is read-only via lab trust).

### 6.3 Runs list

`frontend/src/routes/_authed.runs.$expId.tsx`:

- The `run_id` cell now resolves the link target inline: if the row has `lolday.job_id` tag, render an internal `<Link>` to `/jobs/<jobId>`; otherwise render an external `<a target="_blank">` to `/mlflow/#/experiments/.../runs/...`.
- The separate `Job ↗` column is removed (redundant with the new run cell).

### 6.4 ArtifactTree download attribute

`components/common/ArtifactTree.tsx` adds `download={name}` to the artifact `<a>`:

```tsx
<a
  href={`/api/v1/runs/${runId}/artifacts/download?path=${encodeURIComponent(e.path)}`}
  download={name}
>
  …
</a>
```

`components/jobs/PredictSummary.tsx` adds `download="predictions.csv"` to the predictions download button.

The HTML `download` attribute is defence-in-depth; the backend `Content-Disposition` (§5.2) is the authoritative source.

### 6.5 i18n

`frontend/src/i18n/zh-TW.json` (and `en.json`): if `OpenInMlflowButton` does not yet route through i18n, plumb it through. Add nested key `common.openInMlflow` per the project rule (no flat dot-keys).

## 7. Operations / Migration

### 7.1 Sequence

```
[1] maldet repo: implement + tests + 2.0.0 PyPI release
[2] lolday repo: bump deps + §5/§6 changes + tests pass + PR merged
[3] Maintenance window opens: BACKEND_MAINTENANCE_MODE=1 + Discord announcement
[4] Backup: pg_dumpall (lolday + mlflow DBs) + mc cp mlflow-artifacts MinIO bucket
[5] Wipe: TRUNCATE Lolday tables + delete MLflow runs/experiments/registry + mlflow gc
[6] build-helper image rebuild + push (helpers.lock updated, committed)
[7] All detector repos: add positive_class to maldet.toml + bump maldet → tag v2.0.0 → trigger build
[8] Each detector: submit baseline train → evaluate → predict; verify acceptance criteria (§7.7)
[9] Maintenance window closes: BACKEND_MAINTENANCE_MODE=0
```

Steps [1]–[2] precede the maintenance window. Steps [3]–[9] are inside the window (estimate 4–6 hours including baseline runs).

### 7.2 Backup

```bash
# Postgres: lolday + mlflow databases (same Postgres instance)
kubectl exec -n lolday <postgres-pod> -- pg_dumpall -U postgres \
  > backup-pgdump-$(date +%Y%m%d-%H%M).sql

# MLflow artifacts in MinIO
mc cp --recursive lolday-minio/mlflow-artifacts \
  /tmp/mlflow-artifacts-backup-$(date +%Y%m%d-%H%M)/
```

Backups stored in operator's home (no sudo path), retained 7 days.

### 7.3 Wipe — Lolday DB

```sql
BEGIN;

TRUNCATE
    model_transition_log,
    model_version,
    job_event,
    job
RESTART IDENTITY CASCADE;

-- Null out cached MLflow experiment IDs on detector_version rows.
-- routers/jobs.py:278 lazy-creates only when the column is NULL; without
-- this, post-wipe job submissions would call create_run() with a stale
-- experiment_id pointing at an experiment that mlflow gc has destroyed.
UPDATE detector_version SET mlflow_experiment_id = NULL;

COMMIT;
```

Preserved tables (data intact): `user`, `dataset_config`, `detector`, `detector_version`, `detector_build`, `user_git_credential`. Only the `detector_version.mlflow_experiment_id` column is reset to NULL.

### 7.4 Wipe — MLflow

Encapsulated in `scripts/wipe-mlflow-history.sh` (sudo-free, uses cluster-internal MLflow service). The script prints a confirmation prompt before any destructive call:

```
This will permanently delete:
  - <N> experiments (excluding Default id=0)
  - <M> runs across all experiments
  - <K> registered models with all versions
Continue? (yes/NO):
```

Script flow (in order):

1. Soft-delete every run in every experiment via `POST /api/2.0/mlflow/runs/delete`
2. Soft-delete every registered model: delete each version, then the model shell
3. Soft-delete every experiment except `id=0` (MLflow default, undeletable)
4. Run `mlflow gc --backend-store-uri <uri>` inside the mlflow-server pod to permanently purge soft-deleted runs and reclaim artifact storage

Soft-delete + `mlflow gc` is the MLflow-recommended deletion pattern.

### 7.5 Build-helper rebuild

```bash
bash scripts/build-helpers.sh
git add charts/lolday/helpers.lock
git commit -m "chore(helpers): rebuild build-helper for maldet 2.0"
git push
```

`helpers.lock` digest must change. New build-helper image pushed to GHCR + Harbor.

### 7.6 Per-detector rollout

For each detector repo (operator runs in parallel):

```bash
# In the detector repo
git checkout main && git pull
# Edit maldet.toml: add positive_class, bump compat.schema_version to 2, bump compat.min_maldet to 2.0
# Edit pyproject.toml: bump maldet>=2.0,<3
git add maldet.toml pyproject.toml
git commit -m "chore(maldet): upgrade to schema_version=2 with explicit positive_class"
git tag v2.0.0
git push --follow-tags
# Trigger detector image build via Lolday backend
curl -X POST -H "Authorization: Bearer <token>" \
  https://lolday.example/api/v1/detectors/<id>/build \
  -d '{"git_tag":"v2.0.0"}'
```

All detectors switch in the same window. No canary.

### 7.7 Baseline + acceptance

For each detector, submit train → evaluate → predict and verify:

| Issue        | Acceptance criterion                                                                                                                                                                                |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 (label)    | Per-class metrics row "Malware" tagged `(positive)` and on first row; Confusion matrix `True Malware × Pred Malware` falls on the bottom-right diagonal; predict distribution matches dataset truth |
| 2 (logs)     | Logs tab shows `stage_begin`, `data_loaded`, `metric`, `stage_end` events; not `(no output)`                                                                                                        |
| 3 (download) | Browser save dialog defaults to `predictions.csv` / `metrics.json` / `model.joblib` from both ArtifactTree and PredictSummary entries                                                               |
| 4 (run page) | Job Detail has no "Open run ↗" tab; header has "Open in MLflow"; visiting `/runs/<expId>/<runId>` deeplink auto-redirects to Job Detail                                                             |

DB sanity (in addition to UI):

```sql
SELECT id, summary_metrics->'confusion_matrix' IS NOT NULL AS cm,
       summary_metrics->'per_class' IS NOT NULL AS pc
FROM job
WHERE type='evaluate' AND status='succeeded'
ORDER BY finished_at DESC LIMIT 5;
```

All 5 most-recent succeeded evaluate jobs must have `cm = true` and `pc = true`.

Lazy-create verification: the very first job submitted post-wipe should succeed. The cached `detector_version.mlflow_experiment_id` was reset to NULL in §7.3, so `routers/jobs.py:278` will hit `get_or_create_experiment(exp_name)` and recreate the MLflow experiment from scratch. The MLflow client's `get_or_create_experiment` already handles the "experiment with this name does not exist → create" path. If submission still fails, the stale-cache reset in §7.3 was incomplete; do not roll back, fix forward.

## 8. Test plan

### 8.1 maldet repo

| Test                                                                           | Target                               |
| ------------------------------------------------------------------------------ | ------------------------------------ |
| `test_manifest::test_positive_class_required_for_binary`                       | §4.1                                 |
| `test_manifest::test_positive_class_must_be_in_classes`                        | §4.1                                 |
| `test_manifest::test_positive_class_optional_for_other_tasks`                  | §4.1                                 |
| `test_manifest::test_schema_version_2`                                         | §4.7                                 |
| `test_sklearn_trainer::test_encode_uses_classes_index`                         | §4.2                                 |
| `test_sklearn_trainer::test_unknown_label_raises`                              | §4.2                                 |
| `test_lightning_trainer::test_encode_uses_classes_index`                       | §4.2                                 |
| `test_predictor::test_pred_label_uses_class_names`                             | §4.5                                 |
| `test_predictor::test_predict_works_with_either_class_ordering`                | §4.5                                 |
| `test_binary_evaluator::test_confusion_matrix_labels_match_matrix_orientation` | §4.4 — Issue 1 root-cause regression |
| `test_binary_evaluator::test_metrics_computed_with_pos_label`                  | §4.4                                 |
| `test_binary_evaluator::test_emits_confusion_matrix_event`                     | §4.4                                 |
| `test_binary_evaluator::test_emits_per_class_event`                            | §4.4                                 |
| `test_event_kinds::test_new_event_kinds_validate`                              | §4.6                                 |

### 8.2 Lolday backend

| Test                                                                                    | Target                                       |
| --------------------------------------------------------------------------------------- | -------------------------------------------- |
| `tests/conftest.py` and 4 other fixtures                                                | add `positive_class: "Malware"` per §5.4     |
| `tests/test_routers_experiments_proxy::test_download_artifact_sets_content_disposition` | §5.2 — Issue 3                               |
| `tests/test_routers_experiments_proxy::test_download_artifact_handles_unicode_filename` | §5.2                                         |
| `tests/test_routers_experiments_proxy::test_download_artifact_handles_path_traversal`   | §5.2 (defence)                               |
| Existing `test_reconciler_summary_projection`                                           | unchanged; verifies projection still correct |
| `tests/test_routers_jobs::test_create_job_blocked_in_maintenance_mode`                  | §5.5                                         |

### 8.3 Lolday frontend

| Test                                                                               | Target |
| ---------------------------------------------------------------------------------- | ------ |
| `routes/_authed.runs.$expId.$runId.test::redirects_to_job_when_lolday_tag_present` | §6.1   |
| `routes/_authed.runs.$expId.$runId.test::redirects_to_mlflow_when_no_tag`          | §6.1   |
| `routes/_authed.runs.$expId.$runId.test::redirects_to_runs_index_on_error`         | §6.1   |
| `components/jobs/JobDetailShell.test::renders_open_in_mlflow_when_run_id_set`      | §6.2   |
| `routes/_authed.jobs.$id.test::tab_list_does_not_include_open_run`                 | §6.2   |
| `components/common/ArtifactTree.test::a_has_download_attribute_with_basename`      | §6.4   |

### 8.4 Playwright e2e

| Spec                                         | Goal                                                                                                                                                       |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/e2e/baseline-train-eval-flow.spec.ts` | Submit train → wait SUCCEEDED → submit eval → wait SUCCEEDED → assert Confusion matrix card present and Malware row positioned per `(positive)` convention |
| `tests/e2e/run-detail-redirect.spec.ts`      | Visit `/runs/<expId>/<runId>` deeplink → auto-redirect to `/jobs/<jobId>`                                                                                  |

## 9. Risk register

| Risk                                                                  | Severity       | Mitigation                                                                                                                                                                         |
| --------------------------------------------------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| maldet 2.0 PyPI publish has a defect                                  | High           | Full test suite green in maldet repo; manual smoke `pip install maldet==2.0.0` before lolday PR merge                                                                              |
| MLflow `gc` hangs or is very slow                                     | Medium         | 30-min timeout in wipe script; if blocked, `kubectl exec` kill + retry                                                                                                             |
| Backup integrity (artifacts)                                          | High           | After backup, run `du -sh` and sample `mc ls`; do not proceed to wipe until verified                                                                                               |
| Custom Trainer subclass (in some detector) does not update signature  | Medium         | maldet 2.0 raises TypeError on missing `classes` kwarg — fail-fast at import or first call                                                                                         |
| Build-helper validator rejects manifest missing `positive_class`      | Low (expected) | Error message names exact field                                                                                                                                                    |
| Operator forgets to set `BACKEND_MAINTENANCE_MODE=1`                  | Medium         | Dual-channel announcement (Discord + UI banner); even if missed, in-flight jobs fail gracefully when DB rows disappear; new submissions on stale frontend get 503 once flag is set |
| MLflow Default experiment (id=0) not deletable                        | Low            | Script skips id=0; harmless empty shell                                                                                                                                            |
| First post-wipe job submission fails because experiment shell missing | Medium         | §7.3 nulls `detector_version.mlflow_experiment_id` so `routers/jobs.py:278` re-enters the `get_or_create_experiment` lazy-create path; verified as part of acceptance              |

## 10. Rollback

| Failure point                                          | Rollback action                                                                                                                 |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| Step [5] (wipe) mid-failure                            | `pg_restore` from `pg_dumpall` + `mc cp` artifacts back + `git revert` lolday PR + redeploy backend/frontend with old image tag |
| Step [6] (build-helper)                                | `helpers.lock` retained in git; rerun `bash scripts/build-helpers.sh`                                                           |
| Step [7] (any detector image fails)                    | That detector individually retried; others proceed (no cross-dependency)                                                        |
| Step [8] (baseline metrics obviously wrong, e.g. f1=0) | Do not roll back — data is wiped, rollback only restores past-buggy state. Debug forward                                        |

Worst-case (steps [5] succeeded, [7] all failed): restore pg + artifacts, retag lolday backend/frontend to pre-PR, ~30 min total. Pre-condition: §7.2 backup must be verified before §7.3.

## 11. Out of scope (explicit non-goals to avoid scope creep)

- MLflow upgrade. Pinned to current version.
- New event kinds beyond `confusion_matrix` and `per_class`. No `system_metric`, `code_version`, etc.
- Run-comparison UI. Runs list table is unchanged except for the cell-link rewrite in §6.3.
- Detector-author docs migration guide. The maldet 2.0 CHANGELOG is the only migration note; lab has small detector author count.
- `OpenInMlflowButton` redesign. Existing component is reused as-is.

---

Source spec for `docs/superpowers/plans/2026-05-01-maldet-2-and-runs-cleanup.md` (to be written next via the writing-plans skill).
