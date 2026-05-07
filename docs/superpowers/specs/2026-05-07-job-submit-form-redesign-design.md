# Job submit form redesign — root-cause refactor + dark-mode + UX gaps

Date: 2026-05-07
Owner: PO-LIN LAI

## 1. Context

Operator surfaced four issues on the Submit Job page and the Detector detail page:

1. **View manifest** (Detector detail → Versions tab → manifest sheet) — body text is illegible under dark mode.
2. **Hyperparameters** card on Submit Job — labels and descriptions are illegible under dark mode.
3. **Submit Job → Train** — once a Test dataset is picked, there is no way to clear it, even though the field is supposed to be optional.
4. **Submit Job → Predict** — the _Model version_ dropdown is always empty; the _Detector_ dropdown does not constrain _Source model_; the _Batch size_ hyperparameter is undocumented; the _Priority_ field has no inline help.

Symptom (1)–(4) look independent but share a single underlying cause described in §2. This spec proposes one redesign that fixes all four together and aligns the form with mainstream MLOps conventions.

Operator constraint (this work order): root-cause first, breaking changes allowed, no concession to backward compatibility, all decisions must follow mainstream practice.

## 2. Root cause

### 2.1 Form modelling defect (drives bug 4 and most of the UX confusion)

`frontend/src/components/forms/JobSubmitForm.tsx` treats `detector_version_id` and `source_model_version_id` as two **independent** form inputs across all three job types (`train`, `evaluate`, `predict`). The domain model already says otherwise:

- `backend/app/schemas/model_registry.py:21` — every `ModelVersion` carries the exact `detector_version_id` it was trained against.
- `backend/app/routers/models_registry.py:88-92` — the API already joins `Detector` to populate the registered model name.

Mainstream MLOps platforms (MLflow Model Registry, SageMaker, Vertex AI, BentoML) treat the model artifact as the primary input for inference. The runtime / framework version is **derived** from the artifact. Overriding the runtime is an advanced workflow.

Lolday's current form ignores this: it lets a user pair `Detector = ELF RF` with `Source model = elf-cnn`. That pairing is internally inconsistent. The user perceives this as "Detector dropdown's purpose is unclear", which is correct — under the current layout the dropdown is decorative for predict / evaluate.

### 2.2 Specific bug roots

| #   | File:Line                                                 | Root cause                                                                                                                                                                                                                    |
| --- | --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `frontend/src/components/common/JsonTreeView.tsx:19`      | `theme="rjv-default"` is hardcoded; no light/dark switching.                                                                                                                                                                  |
| 2   | `frontend/src/index.css:75-90`                            | `.rjsf-wrap` only sets widths. RJSF v5 default templates render `<small>` and `<label>` with no Tailwind / CSS-variable styling, so dark-mode tokens never apply to RJSF-generated DOM.                                       |
| 3a  | `frontend/src/components/forms/JobSubmitForm.logic.ts:7`  | `train` returns `["train_dataset_id", "test_dataset_id"]` from `requiredFieldsForType`, contradicting `StageExplainer.tsx:13` and the `stage.train.description` i18n string (both say test_dataset is optional).              |
| 3b  | `frontend/src/components/forms/JobSubmitForm.tsx:240-246` | shadcn `<Select>` (Radix primitive) has no built-in clear; no clearable wrapper exists in the codebase.                                                                                                                       |
| 4a  | `frontend/src/components/forms/JobSubmitForm.tsx:96-101`  | Casts `modelVersions` to `{ items?: [...] }` and reads `.items`. But `useModelVersions` (`frontend/src/api/queries/models.ts:68`) already unwraps and returns a plain array, so the cast yields `undefined` → empty dropdown. |
| 4b  | Same file, lines 264-316                                  | No relationship between `detectorId` and `Source model` selector — see §2.1.                                                                                                                                                  |

## 3. Design

### 3.1 Form layout (per job type)

Two sub-forms keyed off Job type:

```
TRAIN
  Job type
  └─ Detector + Version              (primary, required)
  └─ Datasets
       ├─ Train dataset              (required)
       └─ Test dataset               (optional, with clear button)
  └─ Hyperparameters                 (auto from detector_version.train.params_schema)

EVALUATE / PREDICT
  Job type
  └─ Source model + Version          (primary, required)
       │  on change → auto-fill ↓
  └─ Detector + Version              (read-only by default)
       └─ ▸ Advanced: override detector version    (collapsed, power user)
  └─ Dataset                         (test for evaluate, predict for predict)
  └─ Hyperparameters                 (auto from detector_version.{evaluate|predict}.params_schema)
```

Reasoning:

- Train has no model yet, so detector/version is the only sensible primary input.
- Inference (evaluate, predict) starts with the model artifact; runtime is derived. This matches MLflow / SageMaker / Vertex AI / BentoML.
- Override exists because Lolday allows multiple detector versions per detector — a user occasionally wants to predict with a newer detector image (e.g., bug-fixed predict path). Hiding it behind an Advanced toggle keeps the default flow clean.

### 3.2 Backend change (denormalize for UI)

Add two derived fields to `ModelVersionRead`:

```python
# backend/app/schemas/model_registry.py
class ModelVersionRead(BaseModel):
    ...existing fields...
    detector_id: uuid.UUID            # NEW — derived from RegisteredModel.detector_id
    detector_version_tag: str         # NEW — derived from DetectorVersion.git_tag
```

Update both call sites (`_model_version_to_read` in `routers/models_registry.py:54-71` and the join query at `:85-95`) to populate the new fields. Cheap join; no migration.

This avoids a new endpoint (`GET /detector-versions/{id}`) — frontend reuses existing `useDetectorVersion(detector_id, tag)` query.

### 3.3 Frontend changes

#### 3.3.1 Form refactor

- Split `JobSubmitForm.tsx` into:
  - `JobSubmitForm.tsx` (orchestrator: job type switch, common state, submit)
  - `TrainSubForm.tsx` (detector → datasets → hyperparams)
  - `InferenceSubForm.tsx` (source model → derived detector → dataset → hyperparams; shared by evaluate + predict)
- Inference flow:
  1. User picks Source model (`registered_models` filtered by `visibility=all`)
  2. User picks Model version
  3. On version pick, frontend reads `modelVersion.detector_id` + `detector_version_tag`, sets the (read-only) detector display
  4. Frontend calls existing `useDetectorVersion(detector_id, tag)` to get manifest → load `params_schema` for the appropriate stage
  5. Optional: user expands "Advanced: override detector version" → shows version dropdown filtered to the same detector (for safety)
- Update `JobSubmitForm.logic.ts`:
  - `train` required: `["train_dataset_id"]` (test_dataset_id removed)
  - `evaluate` required: `["source_model_version_id", "test_dataset_id"]`
  - `predict` required: `["source_model_version_id", "predict_dataset_id"]`
- Update `?from=<job_id>` prefill to also restore `source_model_version_id` (currently missing).

#### 3.3.2 Bug fixes

| Bug | Fix                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | New hook `useResolvedTheme()` in `frontend/src/hooks/`: reads `document.documentElement.classList`; falls back to `matchMedia` for `system`. `JsonTreeView.tsx` uses it to switch between `rjv-default` (light) and `monokai` (dark).                                                                                                                                                                                                   |
| 2   | Extend `.rjsf-wrap` in `index.css`:<br>- `& label, & legend { @apply text-foreground; }`<br>- `& .field-description, & small, & .help-block { @apply text-muted-foreground; }`<br>- `& input, & select, & textarea { @apply bg-background border-input text-foreground; }`<br>- `& fieldset { @apply border-border; }`<br>RJSF docs explicitly endorse styling-via-CSS as a supported customization path; we are not forking templates. |
| 3a  | `JobSubmitForm.logic.ts` removes `test_dataset_id` from `train` required list. `submit()` already sends `null` correctly.                                                                                                                                                                                                                                                                                                               |
| 3b  | New component `frontend/src/components/forms/ClearableSelect.tsx`: wraps shadcn `<Select>`, adds a `lucide-react` `<X>` icon button to the right when value is set and `clearable` prop is true. Used for `Test dataset` in train mode.                                                                                                                                                                                                 |
| 4a  | Replace the broken cast at `JobSubmitForm.tsx:96-101` with `const modelVersionsArr = modelVersions ?? [];`. After the form refactor (§3.3.1) this code moves to `InferenceSubForm.tsx`.                                                                                                                                                                                                                                                 |
| 4b  | Resolved by §3.1 + §3.2 + §3.3.1 — Source model directly drives detector.                                                                                                                                                                                                                                                                                                                                                               |

#### 3.3.3 Help-hint pattern

New reusable component `frontend/src/components/common/HelpHint.tsx`:

- Default: shadcn `Tooltip` + `lucide-react` `<HelpCircle className="h-4 w-4 text-muted-foreground" />` next to the label.
- Long content (e.g., Priority warning): pass `popover` prop to switch to shadcn `Popover` for click-to-open.
- A11y: `aria-describedby` wired to the tooltip / popover content id.
- Mainstream pattern (Material 3, Carbon, Atlassian, shadcn examples).

Applied to:

| Field                     | Mode                                        | Content (zh-TW)                                                                                                             |
| ------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Priority (admin only)     | always                                      | reuses `jobs.priority.warning` i18n key (popover, longer text)                                                              |
| Test dataset              | Train                                       | "可選。提供時會多算最終 metrics + 混淆矩陣。不選則只訓練模型，不算指標。"                                                   |
| Source model              | Evaluate / Predict                          | "已訓練好的模型；推論時會載入它的 weights。模型已綁定一個 detector，下方 detector 區塊會自動帶入。"                         |
| Override detector version | Evaluate / Predict (when Advanced expanded) | "預設用模型訓練時的 detector version（保證可重現）。覆寫適用於 predict pipeline 有 bugfix、或想用較新的 evaluator 的場合。" |

We **do not** hardcode descriptions for hyperparameter fields (e.g., `batch_size`). Those belong to the detector author's `params_schema.description`, which RJSF auto-renders. If a maldet detector ships an undocumented param, the long-term fix is upstream in maldet, not Lolday.

### 3.4 i18n keys (new)

```
help.test_dataset_optional         (Train form)
help.source_model                  (Inference form)
help.override_detector_version     (Advanced toggle)
```

Existing `jobs.priority.warning` is reused for the Priority popover.

## 4. Implementation order (two PRs)

Two separate PRs to keep blast radius small. PR 1 lands first; PR 2 depends on PR 1.

### PR 1 — Backend denormalization + frontend foundations + four bug fixes

Scope:

- Backend `ModelVersionRead` adds `detector_id` + `detector_version_tag`; update `_model_version_to_read` and the join query at `routers/models_registry.py:85-95`.
- Backend tests: extend the existing model_registry tests to assert the two new fields.
- Frontend `pnpm gen-api-types` regenerates `schema.gen.ts`.
- New hook `frontend/src/hooks/useResolvedTheme.ts`.
- New components `frontend/src/components/forms/ClearableSelect.tsx`, `frontend/src/components/common/HelpHint.tsx`.
- Fix `JsonTreeView` dark mode.
- Extend `.rjsf-wrap` CSS for dark mode.
- Fix model version envelope bug (4a).
- Fix `requiredFieldsForType("train")` (3a).
- Apply `ClearableSelect` to Test dataset (Train).
- Apply `HelpHint` to Priority (popover) and Test dataset (tooltip) in the existing form — both fields exist pre-refactor, no need to gate on PR 2.
- Frontend unit tests (vitest + Testing Library) for `ClearableSelect`, `HelpHint`, `useResolvedTheme`.

PR 1 is purely additive on the backend and the frontend changes are localised. No breaking changes.

### PR 2 — Job submit form refactor

Scope (depends on PR 1):

- Split `JobSubmitForm.tsx` into orchestrator + `TrainSubForm.tsx` + `InferenceSubForm.tsx`.
- Implement model-driven detector derivation in `InferenceSubForm.tsx`.
- "Advanced: override detector version" collapsible section.
- Apply `HelpHint` to Source model (Inference) and Override toggle (Inference). Test dataset and Priority help hints land in PR 1.
- Update `JobSubmitForm.logic.ts` required-fields map for evaluate / predict.
- Update `?from=<job_id>` prefill to include `source_model_version_id`.
- i18n keys (zh-TW + en).
- Vitest snapshot / interaction tests for both sub-forms.
- Playwright e2e: train, evaluate, predict happy paths.

PR 2 is a breaking UX change (form layout differs). Operator approved breaking changes; no migration banner needed.

## 5. Testing strategy

- **Backend pytest**: assert `ModelVersionRead.detector_id` + `detector_version_tag` are populated on:
  - `GET /api/v1/models/{owner}/{name}/versions`
  - `GET /api/v1/models/versions/{version_id}`
  - `GET /api/v1/models/versions?source_job_id=...`
- **Frontend vitest**:
  - `useResolvedTheme` returns "dark" when `<html class="dark">`, "light" when `<html class="light">`, tracks `matchMedia` change for `system`.
  - `ClearableSelect` renders `X` button only when value set and `clearable=true`; click clears value.
  - `HelpHint` renders Tooltip by default, Popover when prop given; has correct `aria-describedby`.
  - `InferenceSubForm` auto-fills detector when model version selected (mock `useDetectorVersion`).
  - `JsonTreeView` switches theme prop based on resolved theme (mock `document.documentElement.classList`).
- **Playwright e2e** (PR 2): three end-to-end submit flows (train / evaluate / predict) against the dev backend; assert correct payload shape.
- **Manual smoke** in dev: submit each job type with screen-reader on; verify dark-mode legibility on Detector detail and Submit Job pages.

## 6. Risks and tech debt

### Risks

- **RJSF dark-mode CSS regression**: extending `.rjsf-wrap` could collide with future RJSF version upgrades. Mitigation: snapshot test on `JobSubmitForm` rendered with a known schema in dark mode; fail loudly if visible markup changes.
- **`?from=<job_id>` prefill** for predict/evaluate may surface previously-broken state (since source_model_version_id was never prefilled). Test specifically: copy a predict job, ensure source model dropdown lights up.
- **Override toggle reachability**: if hidden too aggressively, advanced users might miss it. Initial render keeps the toggle visible (collapsed); we add an i18n string explaining when to expand.

### Tech debt notes (record in `docs/architecture.md` §9)

1. `@microlink/react-json-view` is a low-activity fork. Evaluate migration to `react-json-view-lite` (CSS-vars-friendly, smaller bundle) once the dark-mode hotfix is shipped. Not blocking.
2. RJSF default templates only partly fit shadcn aesthetic. A future task: evaluate `@rjsf/shadcn` (community) or implement a custom template set in `frontend/src/components/forms/rjsf-templates/`. Not blocking.
3. maldet `BatchPredictor.params_schema` has empty `properties` in the test fixture. Encourage maldet authors (upstream) to add `description` for `batch_size` and other known params, so Lolday auto-renders schema descriptions instead of bolting on per-param help text.

## 7. Out of scope

- Replacing `@microlink/react-json-view`.
- Migrating to `@rjsf/shadcn`.
- Changing maldet's `BatchPredictor.params_schema`.
- Any change to job-priority semantics — only the help icon is added.
- Server-side validation that `detector_version_id` matches `source_model_version_id.detector_version_id` (defence in depth; consider as follow-up if PR 2 reveals API misuse).

## 8. Acceptance criteria

A reviewer can verify the following on a preview deploy after PR 2:

1. Detector detail → Versions tab → View manifest: text remains legible under dark mode.
2. Submit Job → Hyperparameters card: labels and descriptions remain legible under dark mode.
3. Submit Job → Train: pick a Test dataset, then clear it via the X button. Submit succeeds with `test_dataset_id=null`.
4. Submit Job → Predict / Evaluate: picking a Model version auto-populates Detector + Version (read-only). Hyperparameters card loads the right schema. The Source model dropdown is the first interactive field after Job type.
5. Each of the four `HelpHint` usages renders a `?` icon, opens on hover (tooltip) or click (popover), and reads correctly under screen reader.
6. `?from=<previous_job_id>` correctly prefills source model version for predict/evaluate copy-job.
7. `pre-commit run --all-files`, `pnpm typecheck`, `pnpm lint`, `cd backend && uv run pytest`, `cd frontend && pnpm test`, `cd frontend && pnpm playwright test` all pass.
