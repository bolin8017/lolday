# Submit-job UX v3 + threshold footgun eradication + platform stance codification

> 2026-05-08 · scope: `frontend/src/components/forms/`, `frontend/src/components/jobs/JobDetailShell.tsx`, `frontend/src/routes/_authed.jobs._index.tsx`, `docs/architecture.md`, `CLAUDE.md`, `docs/detector-repos.md`, plus cross-repo cleanup in `maldet`, `elfrfdet`, `elfcnndet`.
>
> Continues the v0.20.x line of submit-job hardening: #109 (root-cause UX fixes) → #110 (TrainSubForm / InferenceSubForm split) → #111 (v0.20.3 cut) → #112 (detector-version override footgun removal). This spec adds four pieces under one theme: simplify priority UX, redesign hyperparameter form, eradicate a second footgun field that mirrors the #112 pattern, and codify the platform-stance principle that justified both removals so future drift is caught at review time instead of post-merge.

## 1. Why

Four distinct issues surfaced in the same submit-job session, all under the platform-discipline theme:

1. **Priority is exposed as a free integer** — admin sees `0` and has no way to know what value is "high enough" to jump the queue. Other admins' priority values are not visible. The mental model the operator actually wants is "make this next" or "leave it as normal", not "pick a number".
2. **Hyperparameters block uses RJSF's vanilla rendering** — every field is the same plain `<input>`, regardless of whether the schema declares it a bounded float (`threshold ∈ [0,1]`), an int with `ge=1`, a bool, or an enum. There is no visual indication of the default value, no per-field reset, and no type-aware widget. The block looks unfinished compared with the rest of the redesigned submit form.
3. **`EvaluateConfig.threshold` is a footgun** — the field is declared in `elfrfdet`, `elfcnndet`, and `maldet` scaffolding templates, validated to `[0.0, 1.0]`, surfaced in the UI through dynamic schema rendering, but **never plumbed through to `BinaryClassification.evaluate()`**. `evaluate()` calls `model.predict()` directly, which uses the default 0.5 argmax — user-supplied threshold values silently have no effect. Structurally identical to the detector-version override toggle removed in #112.
4. **The platform-stance principle is implicit, not citable** — the discipline that justified removing the override toggle (#112) and now justifies removing `threshold` is "lolday is a deploy platform, not a detector-development platform". This idea exists in the original 2026-03-30 spec and a one-liner in `CLAUDE.md`, but it is not stated as a citable rule with stage-aware criteria. Each new footgun is therefore caught only at review-time recognition, not principle-driven rejection. Codify it so future PRs (whether human or AI-generated) get rejected up-front.

## 2. Decisions

| #   | Topic              | Decision                                                                                                                                                                                                                                                                                                                                                                      |
| --- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Q1  | Hyperparameters UI | Direction A — RJSF widget remap to type-aware controls (slider+input for bounded float, stepper for int, switch for bool, select for enum). Per-field "Default: X" badge, per-field reset. Single column.                                                                                                                                                                     |
| Q2  | Priority UX        | Two-button toggle (Normal · ⚡ Priority), semantics α (Normal = `0`, Priority = `1`, fixed). Backend integer field unchanged. All three admin-facing surfaces (submit / detail / jobs list) updated.                                                                                                                                                                          |
| Q3  | Threshold field    | **Eradicate**: remove from `elfrfdet` / `elfcnndet` `EvaluateConfig`, remove from maldet `templates/{rf,cnn}/src/configs.py.j2`. Bump detector versions and rebuild images so the field never appears in any new manifest. `BinaryClassification.evaluate()` already uses `model.predict()` — no maldet evaluator change needed. Legacy 4.0.0 manifests are handled per §6.4. |
| Q4  | Platform stance    | Codify "deploy platform, not development platform" + the stage-aware UX rule (no behavioral knobs in `EvaluateConfig` / `PredictConfig`) in `docs/architecture.md` §1.2 + §1.3 (new subsections) and `CLAUDE.md` Hard rules. Reviewer cites these subsections to reject future leaky-abstraction PRs.                                                                         |

## 3. Out of scope

- **Aging / automatic priority bump** — Phase 7+ if needed. MVP stays manual-bump.
- **Per-user priority quota** — Phase 7+ if needed.
- **Plumbing threshold through the evaluator** (Q3 alternative A from brainstorming) — explicitly rejected: lolday's role is to deploy detectors that authors have already tuned; threshold tuning is a detector-development concern that belongs inside the detector repo (or baked into the model artifact via `TunedThresholdClassifierCV`).
- **Multi-tier priority** (Normal / High / Urgent) — current operator usage does not justify three tiers; if this becomes load-bearing, add later as α with extended toggle group rather than re-introducing free integer.
- **Non-admin priority visibility** — unchanged. Priority remains admin-only.

## 4. Q1 — Hyperparameters UI redesign

### 4.1 Current state

`RjsfConfigForm` (`frontend/src/components/forms/RjsfConfigForm.tsx`) wraps `@rjsf/core` `Form` with `validator-ajv8` and Tailwind theming via `.rjsf-wrap` in `index.css`. It renders whatever the detector manifest declares (e.g. `TrainConfig` / `EvaluateConfig` / `PredictConfig` Pydantic schemas) using RJSF's default field templates. Every field renders as a plain `<input>` regardless of type or bounds.

`deriveUiSchemaFromSchema` already walks the schema and only sets `ui:placeholder` to communicate defaults. There is no widget remap.

### 4.2 Target

Single-column form, each field rendered as:

```
┌────────────────────────────────────────────┐
│ field_name        [default 0.5]    ↺ reset │   ← label row (badge "default X" or "modified")
│ ┌──────────────[━━━━━━━●━━━━━]──[ 0.50 ]┐ │   ← type-aware control
│ │ slider track + thumb         numeric  │ │
│ └────────────────────────────────────────┘ │
│ Description text from schema (muted)       │   ← description (always visible)
└────────────────────────────────────────────┘
```

Type → widget mapping rules (consumed by RJSF `widgets` prop):

| JSON Schema type                     | Bounds                           | Widget                                                            |
| ------------------------------------ | -------------------------------- | ----------------------------------------------------------------- |
| `number` / float                     | `minimum` and `maximum` both set | `RangeSliderWidget` (slider + numeric input combo, two-way bound) |
| `number` / float                     | unbounded or one-sided           | `NumericInputWidget` (numeric input, mono font)                   |
| `integer`                            | any                              | `StepperWidget` (− / value / + buttons via shadcn)                |
| `boolean`                            | —                                | `SwitchWidget` (shadcn `Switch`)                                  |
| `string` with `enum`                 | —                                | `SelectWidget` (shadcn `Select`, already RJSF default)            |
| `string`                             | no `enum`                        | RJSF default `<input type=text>`                                  |
| nullable types (`type: [X, "null"]`) | —                                | wrap above with a "set / null" toggle                             |

Reasoning: `threshold: float [0,1]`, `n_estimators: int ge=1`, `random_state: int`, `lr: float gt=0` — these are all real fields in `elfrfdet` / `elfcnndet` configs today. The mapping above type-correctly handles each.

### 4.3 Per-field UX

- **"Default: X" badge** appears next to label as long as current value matches the schema `default`. Switches to "modified" badge (accent color) when value diverges.
- **Per-field reset** (`↺` icon button) appears only when value diverges from default. Click → resets just that field.
- **Description** (`schema.description` or Pydantic `Field(..., description=...)`) renders below the control in muted color. Always visible — do not collapse to tooltip.
- **Validation errors** keep RJSF's default error rendering (already styled by `.rjsf-wrap`).

### 4.4 Form-level UX

- Reset button is removed in favor of a single **"Reset all to defaults"** button at the bottom-right (current design already has this; preserve).
- Live JSON preview (direction C from brainstorming) is rejected for v3 — adds horizontal layout cost on a `max-w-3xl` form, and operator did not surface a reproducibility need that justifies it. May add later if asked.

### 4.5 Files touched

| File                                                                 | Change                                                                                                  |
| -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `frontend/src/components/forms/RjsfConfigForm.tsx`                   | Pass `widgets` prop with the custom widget map; pass `templates` if needed for label/badge/reset row    |
| `frontend/src/components/forms/RjsfConfigForm.logic.ts`              | Stop setting `ui:placeholder` (default is now shown explicitly via badge)                               |
| `frontend/src/components/forms/widgets/RangeSliderWidget.tsx` (new)  | Slider + numeric input combo, controlled                                                                |
| `frontend/src/components/forms/widgets/StepperWidget.tsx` (new)      | − / value / +                                                                                           |
| `frontend/src/components/forms/widgets/NumericInputWidget.tsx` (new) | Numeric input, mono font                                                                                |
| `frontend/src/components/forms/widgets/SwitchWidget.tsx` (new)       | shadcn `Switch` adapter                                                                                 |
| `frontend/src/components/forms/templates/FieldTemplate.tsx` (new)    | Custom RJSF field template implementing label row with badge + reset                                    |
| `frontend/src/components/ui/slider.tsx` (new shadcn primitive)       | Run `pnpm dlx shadcn@latest add slider`                                                                 |
| `frontend/src/components/ui/switch.tsx`                              | Verify or add via shadcn CLI                                                                            |
| `frontend/src/i18n/{zh-TW,en}.json`                                  | New keys: `forms.rjsf.default`, `forms.rjsf.modified`, `forms.rjsf.reset_field`, `forms.rjsf.reset_all` |
| `frontend/src/index.css` `.rjsf-wrap`                                | Tighten styles where the new widget layout differs (e.g., margins around badges)                        |

### 4.6 Mainstream alignment

- Replicate model UI (slider + numeric combo for bounded floats), HuggingFace Spaces inference panel, Modal app run forms — same widget-per-type pattern.
- shadcn/ui owns all widget primitives (Slider, Switch, Select, Input, Button) — no new component library.

## 5. Q2 — Priority button (α)

### 5.1 Decision recap

- **Semantic α**: Normal = `0`, Priority = `1` (fixed). Sort behavior `(priority DESC, submitted_at ASC)` already guarantees Priority jobs go first and FIFO within tier.
- **Backend integer field unchanged** — keeps API parity with K8s PriorityClass / Slurm / AWS Batch and provides an admin escape hatch for the rare case of needing to override an existing Priority job (admin can `PATCH /jobs/{id}` with a custom integer via curl).
- All three admin-facing surfaces converge on the same UX (submit / detail / jobs list).
- Non-admin behavior unchanged: field invisible, sending `priority != 0` returns 403 (existing logic in `backend/app/routers/jobs.py:341-343`).

### 5.2 Files touched

| File                                                            | Change                                                                                                                                                                     |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/components/forms/JobSubmitForm.tsx`               | Replace number `Input` with two-button toggle (`Normal` / `⚡ Priority`); replace local `priority: number` state with `priorityHigh: boolean`; on submit map to `1` or `0` |
| `frontend/src/components/jobs/JobDetailShell.tsx`               | `PriorityEditor` rewrite: replace number `Input` + Save button with toggle; auto-save on toggle (no separate Save button needed since there's only one of two states)      |
| `frontend/src/routes/_authed.jobs._index.tsx`                   | `PriorityCell` rewrite: render badge (`Normal` chip / `⚡ Priority` chip / `—` for non-editable rows) wrapped in shadcn `Popover`; popover content is the same toggle      |
| `frontend/src/components/forms/PriorityToggle.tsx` (new)        | Shared two-button toggle component used by all three sites; takes `value: 0 \| 1`, `onChange`, `disabled?`, `compact?` (size variant for table cell popover)               |
| `frontend/src/components/ui/popover.tsx` (new shadcn primitive) | `pnpm dlx shadcn@latest add popover`                                                                                                                                       |
| `frontend/src/i18n/{zh-TW,en}.json`                             | Update `jobs.priority.*` keys: add `normal`, `high`; the existing `warning` and `priority_admin` help-text remain                                                          |

### 5.3 i18n key changes

```diff
  "jobs": {
    "priority": {
      "label": "優先度",
+     "normal": "正常",
+     "high": "優先",
      "column": "優先度",
      "adminOnly": "僅管理員",
-     "editPlaceholder": "0",
-     "save": "儲存優先度",
      "warning": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。"
    },
    "help": {
-     "priority_admin": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。",
+     "priority_admin": "Priority 排在 Normal 工作前面；多個 Priority 工作之間照送出時間排（先送先跑）。Running 工作不受影響。",
       ...
    }
  }
```

`editPlaceholder` and `save` are removed because there is no free-text input and no separate save action (toggle auto-saves).

### 5.4 Backend / API

**No changes.** The integer `priority` field stays in `JobCreate`, `JobPatch`, `JobRead`, and the `Job` model. Frontend constrains its outgoing values to `{0, 1}`, but `PATCH /jobs/{id}` continues to accept any non-negative integer for the curl escape hatch. The runbook `docs/runbooks/admin-priority.md` keeps describing the curl path with arbitrary integers; the UI section is updated to describe the two-button toggle.

### 5.5 Mainstream alignment

- **K8s PriorityClass**: named tier in API → fixed integer in storage. `PriorityClass:high` etc. Same pattern as α.
- **Argo Workflows `WorkflowSpec.priority`**: integer that defaults to 0; UI products typically expose two or three preset levels.
- **GCP Cloud Tasks**: `dispatch_deadline` + named tier system; integer underneath.
- **Slurm `Priority`**: integer; the QOS layer above maps named QOSs (debug / normal / high) to priority values.

## 6. Q3 — Threshold field removal

### 6.1 Decision recap

`EvaluateConfig.threshold` is removed from both detector repos and from the maldet scaffolding templates. `BinaryClassification.evaluate()` keeps using `model.predict()` (default argmax 0.5) — no evaluator code change.

Rationale (full discussion in brainstorming transcript):

- Lolday is a deploy / glue platform; detector tuning belongs in the detector author's own repo (`elfrfdet`, `elfcnndet`).
- Detector authors who want a non-0.5 operating point should bake it into the model artifact (sklearn `TunedThresholdClassifierCV` for RF; threshold-aware wrapper for CNN) or implement a custom `Evaluator` protocol implementation in their detector code — both options keep the tuning decision inside the detector repo, not exposed to platform users.
- The current implementation is a leaky abstraction identical to the detector-version override toggle removed in #112: declared, validated, surfaced in UI, silently ignored.

### 6.2 Files touched (cross-repo)

**`maldet` repo** (PR 1, blocking detector PRs):

| File                                         | Change                                                                                                                                                                           |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/maldet/templates/rf/src/configs.py.j2`  | Remove `threshold: float = Field(...)` line from `EvaluateConfig`                                                                                                                |
| `src/maldet/templates/cnn/src/configs.py.j2` | Same                                                                                                                                                                             |
| `tests/fixtures/sample_configs.py`           | Remove `threshold` from any fixture that has it                                                                                                                                  |
| `CHANGELOG.md`                               | Note: removed `threshold` from binary classification scaffolding templates; consumer detectors should drop the field on their next version. Bump minor (e.g. `2.0.x` → `2.1.0`). |

`src/maldet/evaluators/binary.py` is **not** touched. It already calls `model.predict()` and ignores any threshold field; that behavior is now consistent.

**`elfrfdet` repo** (PR 2, depends on no maldet change):

| File                      | Change                                                                                                                                                                        |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/elfrfdet/configs.py` | Remove `threshold: float = Field(...)` from `EvaluateConfig`. The class becomes empty `pass` (or remove and adjust manifest if the schema introspection allows empty config). |
| `tests/test_configs.py`   | Remove `test_evaluate_config_threshold_range`                                                                                                                                 |
| `maldet.toml`             | Bump `[detector]` version (e.g. `4.0.0` → `4.1.0`)                                                                                                                            |
| `CHANGELOG.md`            | Note removal                                                                                                                                                                  |

**`elfcnndet` repo** (PR 3, parallel to elfrfdet):

| File                       | Change           |
| -------------------------- | ---------------- |
| `src/elfcnndet/configs.py` | Same as elfrfdet |
| `tests/test_configs.py`    | Same             |
| `maldet.toml`              | Same bump        |
| `CHANGELOG.md`             | Same             |

**`docs/detector-repos.md` in lolday** (no PR, just doc bump):

| File                     | Change                                                |
| ------------------------ | ----------------------------------------------------- |
| `docs/detector-repos.md` | Bump version columns to reflect new detector versions |

### 6.3 Operator follow-up

After detector PRs merge, the operator runs:

```bash
# from each detector repo: tag and push
git tag 4.1.0 && git push origin 4.1.0
# from lolday host: build new images via the platform
curl -X POST "https://<lolday-host>/api/v1/detectors/<detector-id>/builds" \
  -H "Cookie: CF_Authorization=<jwt>" \
  -d '{"git_tag": "4.1.0"}'
```

After both detectors have a 4.1.0 image in Harbor, mark them as the new active default for new training jobs. The detector-versions UI keeps both 4.0.0 and 4.1.0 listed for `train` selection until 4.0.0 is retired (§6.4).

### 6.4 Legacy 4.0.0 manifests

An existing model trained against 4.0.0 has `detector_version_id` pointing at the 4.0.0 row, whose manifest still contains the `threshold` field. Two options for handling these legacy bindings:

|                               | Option L1 — Clean break                                                                                                                                                                                                   | Option L2 — Gradual retirement                                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Action**                    | Mark 4.0.0 detector versions `inactive` immediately upon 4.1.0 merge                                                                                                                                                      | Leave 4.0.0 `active`; let users retrain to 4.1.0 over time, retire 4.0.0 after a defined grace period (e.g. 2 weeks) |
| **Effect on existing models** | Old models bound to 4.0.0 can no longer dispatch evaluate / predict jobs (the inference dispatcher rejects retired-version bindings — see existing handling at `frontend/src/components/forms/JobSubmitForm.tsx:135-139`) | Old models continue to work; their evaluate form still shows the inert `threshold` field (legacy footgun visibility) |
| **Effect on new work**        | Forces immediate retraining onto 4.1.0                                                                                                                                                                                    | New training auto-picks 4.1.0; no forced migration                                                                   |
| **Operational disruption**    | Higher (anyone with active 4.0.0 model must retrain)                                                                                                                                                                      | Low (transparent to users)                                                                                           |
| **Time to "fully gone"**      | Immediate                                                                                                                                                                                                                 | After grace period + retirement                                                                                      |

**Decision: Option L2** with a 2-week grace period.

Rationale:

- The 4.0.0 footgun is _cosmetic-only_ (threshold field has been silently ignored since day one — leaving it visible for 2 more weeks does not introduce new risk).
- Forcing immediate retraining of every existing model is high-disruption for a research lab where models are personal artefacts.
- Grace period gives operators time to identify any model they want to preserve and re-train against 4.1.0 deliberately.
- After the grace period, operator runs the standard retire-version flow (DB `UPDATE detector_versions SET status='inactive' WHERE git_tag='4.0.0' AND detector_id IN (...)`).

This decision is reversible: if the grace period proves disruptive in either direction (too long → footgun lingers, too short → operator unprepared), adjust the retirement date in `docs/runbooks/admin-priority.md` follow-up.

### 6.5 Mainstream alignment

- **K8s `PodSpec`**: only fields that affect runtime behavior are exposed; deprecated/no-op fields are removed in minor versions with API changes documented.
- **scikit-learn deprecation policy**: declared parameters that have no effect are flagged as deprecated and removed in the next minor — the same pattern.

## 7. Cross-repo coordination

Work spans **four repos**. PRs are independent (no inter-PR ordering constraint), but the runtime sequence matters:

```
PR 1. maldet — templates cleanup + minor bump (e.g. 2.0.x → 2.1.0)
PR 2. elfrfdet — EvaluateConfig.threshold removed + tests + version bump 4.0.0 → 4.1.0
PR 3. elfcnndet — same as elfrfdet
PR 4. lolday — frontend Q1 + Q2 + docs Q4 (architecture.md §1, CLAUDE.md, detector-repos.md)

Operator runtime sequence (after PRs merge):
  a. Tag + push 4.1.0 in elfrfdet and elfcnndet repos
  b. Run POST /detectors/<id>/builds for each detector against the 4.1.0 tag (creates 4.1.0 image in Harbor + DB row)
  c. Verify 4.1.0 active in `Detectors` page; verify Hyperparameters block on a fresh evaluate job no longer shows threshold
  d. After 2-week grace period (per §6.4), retire 4.0.0 detector versions via `kubectl exec` SQL or admin endpoint
```

Lolday PR (#4) bundles the docs work because all docs files (`architecture.md`, `CLAUDE.md`, `detector-repos.md`) live in the lolday repo — separate doc PR would just create a redundant review cycle.

## 8. Backwards compatibility

User authorized destructive changes. Specifically:

- **Q2 i18n keys** (`editPlaceholder`, `save`) removed without deprecation.
- **Q3 detector schema** loses a field; existing manifests in MLflow / Harbor that reference the old version still work because they pin a `detector_version_id` (not the manifest body). New detector versions simply do not have the field.
- **No alembic migration needed** — `Job.priority` integer column unchanged.

## 9. Detector version bump policy

Removing `EvaluateConfig.threshold` is technically a breaking change at the detector contract level (a manifest consumer expecting the field would fail validation). The field, however, is an _input_ to the evaluate stage; consumers that only read the manifest are unaffected.

**Decision: minor bump `4.0.0 → 4.1.0`** with a CHANGELOG entry on each detector repo. Justification: the field had no observable behavior (footgun #112-pattern), so no real consumer relied on it; treating its removal as breaking would be ceremony without reason. If a hard semver dependency surfaces in operations later, retag the next bump as `5.0.0`.

## 10. Platform stance codification (Q4)

Lolday's stance has been an implicit operating principle since the original 2026-03-30 platform spec; the recent footgun-removal precedents (PR #112 detector-version override, this spec's threshold field) prove that an implicit principle is insufficient — both leaks reached merge before being caught. This section ships the literal text that becomes the citable rule.

### 10.1 `docs/architecture.md` §1 — extend "Purpose & positioning"

Existing §1 has three paragraphs (lolday is glue, deploy target, non-goals) followed by a §2 system diagram. Convert the existing flat paragraphs into §1.1, then add §1.2 and §1.3:

```markdown
## 1. Purpose & positioning

Lolday is **ISLab's internal ML platform for managing the lifecycle of malware detectors**. A user defines a detector (Python code following the `maldet` spec), lolday builds it into an OCI image, runs training/evaluation/prediction jobs as Volcano `vcjob` workloads on GPUs, tracks experiments via MLflow, and stores models in MLflow's registry plus images in a private Harbor registry.

- **Deploy target**: server30 (`140.118.155.30`, SSH 9453), Ubuntu 24.04, K3s single-node, NVIDIA GPU operator on host. Shared lab server.
- **Non-goals**: multi-tenant SaaS, multi-cluster, cloud-managed deployment, public exposure beyond Cloudflare Access SSO.

### 1.1 Glue, not framework

Lolday is **glue code, not a framework**. Detector logic lives in the external `maldet` PyPI package and in per-detector repos; lolday integrates against them. Custom code in this repo is justified only when it serves the glue layer (job dispatch, manifest hosting, registry coordination, GPU queueing) or implements `maldet`-spec-specific orchestration. ML logic — feature extraction, training algorithms, threshold selection, calibration — does not live here.

### 1.2 Deploy platform, not development platform

A detector lifecycle in ISLab:
```

[detector repo: elfrfdet, elfcnndet, …] [lolday]
author tunes hyperparameters, → build image from tagged repo
runs ROC analysis, run train jobs on shared datasets
picks operating point / threshold, run evaluate / predict on trained models
calibrates, track results, manage GPU queue
tags release version 4.1.0

```

Lolday is the runtime for **already-tuned** detectors. Authors finish their work — pick the operating point, calibrate, validate on their own data, write a CHANGELOG entry, tag a release — *before* a version reaches the platform. Lolday's user is a teammate who wants to **run** a detector on a shared dataset, not develop one.

The platform does NOT provide:
- Hyperparameter tuning UIs (ROC sweeps, threshold optimization, grid search, calibration utilities)
- Per-run override of detector author design decisions
- A detector-debugging environment (use the detector repo's own dev setup with `maldet run` locally)

When a feature would let a platform user re-tune what an author already decided, that is a **leaky abstraction**. Remove it. Past examples:

- **Detector-version override toggle** — removed 2026-05-08 (PR #112). Let users mismatch a model's training detector version with the inference detector version, breaking reproducibility.
- **`EvaluateConfig.threshold` field** — removed 2026-05-08 (this spec). Declared but never plumbed; let users believe they were tuning the operating point when they were not.

### 1.3 Stage-aware UX rule

Job stages map to different responsibilities. The hyperparameter form must reflect that mapping:

| Stage | Allowable user-controlled hparams | Why |
|---|---|---|
| `train` | Anything — `n_estimators`, `lr`, `epochs`, `random_state`, … | Training is by definition where the user picks hparams for their experiment. The output (a trained model artefact) embeds those choices and is the contract for downstream stages. |
| `evaluate` | Resource / perf only — `batch_size`, parallelism | The trained model is a fixed artefact. Operating-point decisions (threshold, calibration) are detector-development concerns; allowing per-eval override means measurements no longer reflect the deployed configuration. |
| `predict` | Resource / perf only — `batch_size`, parallelism | Same reasoning. The model + author decisions are the contract; predict applies them. |

When adding a new field to a detector's stage config, ask: *"Does this knob change detector behavior, or only resource usage?"*

- Behavioral knob → goes in `TrainConfig` (baked into the artefact at training time) or out of the config entirely (hardcoded in detector code, or selected at training via `TunedThresholdClassifierCV`-style wrappers and stored as model metadata).
- Resource / perf knob → may live in any stage's config.

The check applies symmetrically: a future maldet evaluator that adds, say, a `noise_injection: float` field to `EvaluateConfig` must be rejected for the same reason — it would change reported metrics in a way the author did not control.
```

### 10.2 `CLAUDE.md` Hard rules — add stance rule

Insert after the existing "Prefer open-source packages over custom code" rule (last entry in the Hard rules section), since both are platform-discipline rules. New rule:

```markdown
### Deploy platform, not development platform

Lolday is the runtime for **already-tuned** detectors. Authors finish all hyperparameter tuning, threshold selection, and calibration in their own repos before tagging a release. The platform must NOT expose UI knobs that let platform users override detector-author design decisions.

**Stage-aware rule**: `TrainConfig` may have user-tunable hparams (per-experiment); `EvaluateConfig` / `PredictConfig` may have only resource / perf knobs (no behavioral knobs).

Precedents (footgun removals):

- PR #112 (2026-05-08) — detector-version override toggle
- 2026-05-08 spec — `EvaluateConfig.threshold` field

Full reasoning: `docs/architecture.md` §1.2 + §1.3.
```

### 10.3 `docs/detector-repos.md` — bump active-version table

Update the rows for `elfrfdet` and `elfcnndet` in the "Active detectors" table:

```diff
-| `~/Documents/repositories/elfrfdet`  | https://github.com/bolin8017/elfrfdet  | sklearn (Random Forest)    | `4.0.0`       | `>=2.0,<3.0` |
-| `~/Documents/repositories/elfcnndet` | https://github.com/bolin8017/elfcnndet | PyTorch Lightning (1D-CNN) | `4.0.0`       | `>=2.0,<3.0` |
+| `~/Documents/repositories/elfrfdet`  | https://github.com/bolin8017/elfrfdet  | sklearn (Random Forest)    | `4.1.0`       | `>=2.1,<3.0` |
+| `~/Documents/repositories/elfcnndet` | https://github.com/bolin8017/elfcnndet | PyTorch Lightning (1D-CNN) | `4.1.0`       | `>=2.1,<3.0` |
```

(maldet pin range bumps minor too because `maldet` minor-bumped from template change. If maldet stays compatible — which it does, since the template change is additive at the framework level — `>=2.0,<3.0` remains correct. Adjust on actual maldet version chosen.)

### 10.4 Operator-process implications

These additions create a citable rule. Concrete review-time enforcement:

- New PR adds field to `EvaluateConfig` or `PredictConfig` → reviewer points at `docs/architecture.md` §1.3 and asks "is this a behavioral knob or a resource knob?"
- New PR adds UI override of a detector-version-derived field → reviewer points at §1.2 "Past examples" list and asks for the same justification PR #112 / threshold removal had to provide.
- AI sessions reading `CLAUDE.md` get the rule in the Hard-rules section, surfaced into every session header without an explicit prompt.

## 11. References

- `frontend/src/components/forms/JobSubmitForm.tsx` — current submit form
- `frontend/src/components/forms/RjsfConfigForm.tsx` — current Hyperparameters block
- `frontend/src/components/jobs/JobDetailShell.tsx` — current detail page priority editor
- `frontend/src/routes/_authed.jobs._index.tsx` — current jobs list priority cell
- `backend/app/routers/jobs.py:337-343, 590-611` — priority backend logic (unchanged)
- `backend/app/reconciler/fifo_scheduler.py` — FIFO scheduler (unchanged)
- `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` — Phase 6 priority design (unchanged)
- `docs/runbooks/admin-priority.md` — operator runbook (UI section to be updated)
- PR #112 (detector-version override removal) — same footgun-removal pattern
- `~/Documents/repositories/elfrfdet/src/elfrfdet/configs.py` — current detector EvaluateConfig
- `~/Documents/repositories/elfcnndet/src/elfcnndet/configs.py` — same
- `~/Documents/repositories/maldet/src/maldet/templates/{rf,cnn}/src/configs.py.j2` — scaffolding templates
- `~/Documents/repositories/maldet/src/maldet/evaluators/binary.py` — `BinaryClassification.evaluate()`, unchanged in this spec
- scikit-learn user guide §1.16 _Tuning the decision threshold for class prediction_ — the document we are explicitly **not** following on the platform side, since detector authors handle this in their own repos
- Replicate model run UI, HuggingFace Spaces inference panel, Modal app run form — Q1 widget-per-type pattern reference
- Kubernetes PriorityClass, Argo Workflows `priority`, Slurm `Priority` + QOS, AWS Batch job-queue priority — Q2 named-tier-over-integer pattern reference
