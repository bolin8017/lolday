# Lolday Submit-job v3 UX + Platform Stance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-integer admin priority input with a Normal / ⚡ Priority toggle (Q2), redesign the RJSF Hyperparameters block with type-aware widgets and per-field default badge + reset (Q1), and codify the "deploy platform, not development platform" + stage-aware UX rule in `docs/architecture.md` and `CLAUDE.md` (Q4).

**Architecture:** Single lolday PR. Three concerns ordered docs-first → priority → hyperparameters so each stage produces a working, testable build. Backend is unchanged. shadcn primitives `Slider` and `Switch` are added (manually authored to avoid CLI artifact conflicts noted in `.claude/projects/.../memory/project_shadcn_cli_collisions.md`). All admin-facing surfaces (submit / detail / jobs list) converge on the same `PriorityToggle`.

**Tech Stack:** Vite + React 18 + TS, shadcn/ui (Radix primitives), `@rjsf/core` v5 + `@rjsf/utils` + `@rjsf/validator-ajv8`, react-i18next, vitest + RTL + userEvent.

**Spec:** `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md` §4 + §5 + §10

---

## File structure

### New (lolday repo)

- `frontend/src/components/ui/slider.tsx` — shadcn Slider primitive (manual)
- `frontend/src/components/ui/switch.tsx` — shadcn Switch primitive (manual)
- `frontend/src/components/forms/PriorityToggle.tsx` — shared two-button toggle
- `frontend/src/components/forms/widgets/RangeSliderWidget.tsx`
- `frontend/src/components/forms/widgets/StepperWidget.tsx`
- `frontend/src/components/forms/widgets/NumericInputWidget.tsx`
- `frontend/src/components/forms/widgets/SwitchWidget.tsx`
- `frontend/src/components/forms/templates/FieldTemplate.tsx`
- `frontend/tests/unit/PriorityToggle.test.tsx`
- `frontend/tests/unit/widgets/RangeSliderWidget.test.tsx`
- `frontend/tests/unit/widgets/StepperWidget.test.tsx`
- `frontend/tests/unit/widgets/SwitchWidget.test.tsx`
- `frontend/tests/unit/widgets/FieldTemplate.test.tsx`

### Modified

- `docs/architecture.md` — extend §1 with §1.1 / §1.2 / §1.3 subsections
- `CLAUDE.md` — add Hard rule "Deploy platform, not development platform"
- `docs/detector-repos.md` — bump active-version table to 4.1.0
- `frontend/src/components/forms/RjsfConfigForm.tsx` — wire `widgets` + `templates`
- `frontend/src/components/forms/RjsfConfigForm.logic.ts` — add type→widget rule in `deriveUiSchemaFromSchema`
- `frontend/src/components/forms/JobSubmitForm.tsx` — replace number Input with `PriorityToggle`
- `frontend/src/components/jobs/JobDetailShell.tsx` — replace `PriorityEditor` body with `PriorityToggle`, auto-save
- `frontend/src/routes/_authed.jobs._index.tsx` — replace `PriorityCell` with badge + Popover wrapping `PriorityToggle`
- `frontend/src/i18n/zh-TW.json` + `frontend/src/i18n/en.json` — new keys, drop `editPlaceholder` / `save`
- `frontend/src/index.css` — minor `.rjsf-wrap` tweaks for badge spacing
- `frontend/package.json` + `pnpm-lock.yaml` — add `@radix-ui/react-slider` + `@radix-ui/react-switch`
- `frontend/tests/unit/components/JobPriorityUI.test.tsx` — rewrite for toggle + popover
- `frontend/tests/unit/components/JobSubmitForm.test.tsx` — rewrite priority assertions
- `frontend/tests/unit/JobDetailShell.test.tsx` — rewrite priority assertions
- `frontend/tests/unit/RjsfConfigForm.test.tsx` — assert type-aware widget rendering
- `frontend/tests/unit/RjsfConfigForm.logic.test.ts` — assert new uiSchema derivation

---

## Section A — Q4 platform stance docs

### Task A1: Branch + extend `docs/architecture.md` §1

**Files:**

- Modify: `docs/architecture.md`

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull
git checkout -b feat/submit-job-v3-priority-hparams-stance
```

- [ ] **Step 2: Read current §1 to confirm exact text**

```bash
grep -n "^## 1\|^### " docs/architecture.md | head -10
```

Expected: §1 currently has 3 paragraphs and 2 bullets, no subsections.

- [ ] **Step 3: Replace §1 body — add §1.1 / §1.2 / §1.3 subsections**

Use Edit. The new §1 content (literal copy from spec §10.1):

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
- **`EvaluateConfig.threshold` field** — removed 2026-05-08. Declared but never plumbed; let users believe they were tuning the operating point when they were not.

### 1.3 Stage-aware UX rule

Job stages map to different responsibilities. The hyperparameter form must reflect that mapping:

| Stage      | Allowable user-controlled hparams                                        | Why                                                                                                                                                                                                                                  |
| ---------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `train`    | Anything — `n_estimators`, `lr`, `epochs`, `random_state`, …             | Training is by definition where the user picks hparams for their experiment. The output (a trained model artefact) embeds those choices and is the contract for downstream stages.                                                  |
| `evaluate` | Resource / perf only — `batch_size`, parallelism                         | The trained model is a fixed artefact. Operating-point decisions (threshold, calibration) are detector-development concerns; allowing per-eval override means measurements no longer reflect the deployed configuration.            |
| `predict`  | Resource / perf only — `batch_size`, parallelism                         | Same reasoning. The model + author decisions are the contract; predict applies them.                                                                                                                                                 |

When adding a new field to a detector's stage config, ask: *"Does this knob change detector behavior, or only resource usage?"*

- Behavioral knob → goes in `TrainConfig` (baked into the artefact at training time) or out of the config entirely (hardcoded in detector code, or selected at training via `TunedThresholdClassifierCV`-style wrappers and stored as model metadata).
- Resource / perf knob → may live in any stage's config.

The check applies symmetrically: a future maldet evaluator that adds, say, a `noise_injection: float` field to `EvaluateConfig` must be rejected for the same reason — it would change reported metrics in a way the author did not control.
```

- [ ] **Step 4: Verify markdown lints clean**

```bash
pnpm dlx prettier --check docs/architecture.md
```

If `--check` fails, run the same command without `--check` to fix in-place:

```bash
pnpm dlx prettier --write docs/architecture.md
```

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(architecture): add §1.1/§1.2/§1.3 — glue, deploy-not-dev, stage-aware UX rule

Codifies the platform stance that justified removing the detector-version
override toggle (#112) and EvaluateConfig.threshold (2026-05-08 spec).
Reviewers cite §1.2 + §1.3 to reject future leaky-abstraction PRs.

Spec: docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md §10.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task A2: Add Hard rule to `CLAUDE.md`

**Files:**

- Modify: `CLAUDE.md`

- [ ] **Step 1: Read current Hard rules section to confirm insertion point**

```bash
grep -n "^### " CLAUDE.md | head -10
```

Expected: rules in order — SSH safety, Sudo policy, Avoid China-origin software, Lint / format 不繞過, Prefer open-source packages over custom code.

- [ ] **Step 2: Insert new rule after "Prefer open-source packages over custom code"**

Use Edit. After the existing `### Prefer open-source packages over custom code` body and before the next `## ` heading, insert:

```markdown
### Deploy platform, not development platform

Lolday is the runtime for **already-tuned** detectors. Authors finish all hyperparameter tuning, threshold selection, and calibration in their own repos before tagging a release. The platform must NOT expose UI knobs that let platform users override detector-author design decisions.

**Stage-aware rule**: `TrainConfig` may have user-tunable hparams (per-experiment); `EvaluateConfig` / `PredictConfig` may have only resource / perf knobs (no behavioral knobs).

Precedents (footgun removals):

- PR #112 (2026-05-08) — detector-version override toggle
- 2026-05-08 spec — `EvaluateConfig.threshold` field

Full reasoning: `docs/architecture.md` §1.2 + §1.3.
```

- [ ] **Step 3: Verify with prettier**

```bash
pnpm dlx prettier --check CLAUDE.md
```

If it fails, run `--write` to fix.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): add 'Deploy platform, not development platform' hard rule

References docs/architecture.md §1.2 + §1.3. Codifies the principle that
governed removal of the detector-version override toggle (#112) and the
EvaluateConfig.threshold field. Future PRs that add behavioral knobs to
EvaluateConfig / PredictConfig get rejected at review time citing this
rule.

Spec: docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md §10.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task A3: Bump `docs/detector-repos.md` table

**Files:**

- Modify: `docs/detector-repos.md`

- [ ] **Step 1: Edit active detectors table**

Replace the two rows for `elfrfdet` and `elfcnndet` to set version `4.1.0` and maldet pin `>=2.1,<3.0`:

```diff
-| `~/Documents/repositories/elfrfdet`  | https://github.com/bolin8017/elfrfdet  | sklearn (Random Forest)    | `4.0.0`       | `>=2.0,<3.0` |
-| `~/Documents/repositories/elfcnndet` | https://github.com/bolin8017/elfcnndet | PyTorch Lightning (1D-CNN) | `4.0.0`       | `>=2.0,<3.0` |
+| `~/Documents/repositories/elfrfdet`  | https://github.com/bolin8017/elfrfdet  | sklearn (Random Forest)    | `4.1.0`       | `>=2.1,<3.0` |
+| `~/Documents/repositories/elfcnndet` | https://github.com/bolin8017/elfcnndet | PyTorch Lightning (1D-CNN) | `4.1.0`       | `>=2.1,<3.0` |
```

- [ ] **Step 2: Commit**

```bash
git add docs/detector-repos.md
git commit -m "docs(detector-repos): bump elf-rf + elf-cnn to 4.1.0 (threshold-removal cohort)

Mirrors the post-merge state once the cross-repo eradication plan
finishes. The maldet pin range bumps minor because the framework
templates dropped the threshold field in 2.1.0.

Spec: docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md §10.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section B — Q2 priority Normal/Priority toggle

### Task B1: Create `PriorityToggle` component (TDD)

**Files:**

- Create: `frontend/src/components/forms/PriorityToggle.tsx`
- Test: `frontend/tests/unit/PriorityToggle.test.tsx`

- [ ] **Step 1: Write failing test**

`frontend/tests/unit/PriorityToggle.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { PriorityToggle } from "@/components/forms/PriorityToggle";

describe("PriorityToggle", () => {
  it("renders Normal and Priority buttons", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /normal/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /priority/i }),
    ).toBeInTheDocument();
  });

  it("marks Normal as pressed when value=0", () => {
    render(<PriorityToggle value={0} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /priority/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("marks Priority as pressed when value=1", () => {
    render(<PriorityToggle value={1} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: /priority/i })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("calls onChange(1) when Priority is clicked from value=0", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /priority/i }));
    expect(onChange).toHaveBeenCalledWith(1);
  });

  it("calls onChange(0) when Normal is clicked from value=1", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={1} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /normal/i }));
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it("does not fire onChange when clicking the already-active button", async () => {
    const onChange = vi.fn();
    render(<PriorityToggle value={0} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /normal/i }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disables both buttons when disabled prop is set", () => {
    render(<PriorityToggle value={0} onChange={() => {}} disabled />);
    expect(screen.getByRole("button", { name: /normal/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /priority/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test — verify fails**

```bash
cd frontend && pnpm test PriorityToggle
```

Expected: FAIL with "Cannot find module '@/components/forms/PriorityToggle'".

- [ ] **Step 3: Implement component**

`frontend/src/components/forms/PriorityToggle.tsx`:

```tsx
import { Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface Props {
  value: 0 | 1;
  onChange: (next: 0 | 1) => void;
  disabled?: boolean;
  size?: "sm" | "default";
}

export function PriorityToggle({
  value,
  onChange,
  disabled,
  size = "default",
}: Props) {
  const { t } = useTranslation();
  const isHigh = value === 1;

  function set(next: 0 | 1) {
    if (next === value) return;
    onChange(next);
  }

  return (
    <div
      className={cn(
        "inline-flex rounded-md border bg-muted p-0.5",
        disabled && "opacity-60",
      )}
      role="group"
      aria-label={t("jobs.priority.label")}
    >
      <Button
        type="button"
        size={size === "sm" ? "sm" : "default"}
        variant="ghost"
        aria-pressed={!isHigh}
        disabled={disabled}
        onClick={() => set(0)}
        className={cn(
          "h-8 rounded-sm px-3",
          !isHigh && "bg-background shadow-sm",
        )}
      >
        {t("jobs.priority.normal")}
      </Button>
      <Button
        type="button"
        size={size === "sm" ? "sm" : "default"}
        variant="ghost"
        aria-pressed={isHigh}
        disabled={disabled}
        onClick={() => set(1)}
        className={cn(
          "h-8 rounded-sm px-3",
          isHigh &&
            "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300 shadow-sm",
        )}
      >
        <Zap className="mr-1 h-4 w-4" />
        {t("jobs.priority.high")}
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Add `jobs.priority.normal` + `jobs.priority.high` keys (temporary stub) so tests pass**

Quick stub edits — full i18n update is Task B5. Add:

`frontend/src/i18n/zh-TW.json` under `jobs.priority`:

```json
"normal": "正常",
"high": "優先",
```

`frontend/src/i18n/en.json` under `jobs.priority`:

```json
"normal": "Normal",
"high": "Priority",
```

- [ ] **Step 5: Run test — verify passes**

```bash
cd frontend && pnpm test PriorityToggle
```

Expected: 7 PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/forms/PriorityToggle.tsx \
        frontend/tests/unit/PriorityToggle.test.tsx \
        frontend/src/i18n/zh-TW.json \
        frontend/src/i18n/en.json
git commit -m "feat(forms): add PriorityToggle — Normal/Priority two-button group

Used by submit form, detail page, and jobs list (next tasks). aria-pressed
on each button for accessibility; Zap icon on the Priority option.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task B2: Replace `JobSubmitForm` priority section with `PriorityToggle`

**Files:**

- Modify: `frontend/src/components/forms/JobSubmitForm.tsx`
- Modify: `frontend/tests/unit/components/JobSubmitForm.test.tsx`

- [ ] **Step 1: Find current priority block**

```bash
grep -n "priority-input\|setPriority\|Input id=\"priority" frontend/src/components/forms/JobSubmitForm.tsx
```

Expected: lines ~47, ~155, ~222–256 reference the priority Input + state.

- [ ] **Step 2: Edit `JobSubmitForm.tsx` — replace priority state + UI**

Change the local state declaration:

```diff
-  const [priority, setPriority] = useState(0);
+  const [priority, setPriority] = useState<0 | 1>(0);
```

Replace the entire priority `<Card>...</Card>` block (currently lines ~222–258) with:

```tsx
{
  isAdmin && (
    <Card>
      <CardHeader>
        <CardTitle>{t("jobs.priority.label")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <PriorityToggle value={priority} onChange={setPriority} />
          <HelpHint popover>{t("jobs.help.priority_admin")}</HelpHint>
        </div>
        {priority === 1 && (
          <p
            className="text-sm rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-900/20 dark:text-amber-300"
            role="alert"
          >
            {t("jobs.priority.warning")}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
```

Add the import at the top:

```tsx
import { PriorityToggle } from "./PriorityToggle";
```

- [ ] **Step 3: Verify the submit logic still maps priority correctly**

Confirm line ~155 still reads:

```tsx
...(isAdmin && priority !== 0 ? { priority } : {}),
```

This already works — `priority === 1` triggers the field, `priority === 0` omits it. No change needed.

- [ ] **Step 4: Update `JobSubmitForm.test.tsx`**

Replace any `getByRole("spinbutton", { name: /priority/i })` assertions with toggle assertions. Add an `it("toggles priority via the new button group", …)` if none exists. Concretely:

```bash
grep -n "spinbutton\|priority.*input" frontend/tests/unit/components/JobSubmitForm.test.tsx
```

For each spinbutton priority assertion, replace with:

```tsx
expect(screen.getByRole("button", { name: /normal/i })).toHaveAttribute(
  "aria-pressed",
  "true",
);
```

Add a new test:

```tsx
it("admin can toggle priority and the submit body carries it", async () => {
  authState.role = "admin";
  // ... existing setup ...
  await userEvent.click(screen.getByRole("button", { name: /priority/i }));
  // ... fill required fields ...
  await userEvent.click(screen.getByRole("button", { name: /submit job/i }));
  expect(submitMutate).toHaveBeenCalledWith(
    expect.objectContaining({ priority: 1 }),
  );
});
```

- [ ] **Step 5: Run tests — confirm pass**

```bash
cd frontend && pnpm test JobSubmitForm
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/forms/JobSubmitForm.tsx \
        frontend/tests/unit/components/JobSubmitForm.test.tsx
git commit -m "refactor(submit-job): replace priority number input with PriorityToggle

Admin-only Card now hosts a two-button toggle (Normal | ⚡ Priority)
instead of a free integer Input + Save button. The submit logic still
sends priority only when value !== 0; the toggle constrains the value
to {0, 1}.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task B3: Replace `JobDetailShell` `PriorityEditor` with `PriorityToggle` (auto-save)

**Files:**

- Modify: `frontend/src/components/jobs/JobDetailShell.tsx`
- Modify: `frontend/tests/unit/components/JobPriorityUI.test.tsx`

- [ ] **Step 1: Edit `JobDetailShell.tsx` — rewrite `PriorityEditor` body**

Replace the entire `function PriorityEditor` (lines 19–81) with:

```tsx
function PriorityEditor({ job }: { job: JobRead }) {
  const { t } = useTranslation();
  const patch = usePatchJob();
  const canEdit = job.status === "queued_backend";
  const current = (job.priority ?? 0) === 0 ? 0 : 1;

  function onChange(next: 0 | 1) {
    if (next === current) return;
    patch.mutate({ id: job.id, priority: next });
  }

  if (!canEdit) {
    return current === 1 ? (
      <Badge
        variant="outline"
        className="bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300"
      >
        ⚡ {t("jobs.priority.high")}
      </Badge>
    ) : (
      <span className="text-sm text-muted-foreground">
        {t("jobs.priority.normal")}
      </span>
    );
  }

  return (
    <div className="space-y-2">
      <PriorityToggle
        value={current}
        onChange={onChange}
        disabled={patch.isPending}
        size="sm"
      />
      {current === 1 && (
        <p
          className="text-sm rounded-md border border-amber-400/60 bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-900/20 dark:text-amber-300"
          role="alert"
        >
          {t("jobs.priority.warning")}
        </p>
      )}
    </div>
  );
}
```

Add imports:

```tsx
import { Badge } from "@/components/ui/badge";
import { PriorityToggle } from "@/components/forms/PriorityToggle";
```

Remove now-unused imports: `Input`, `useState`. Remove the `saved` local state (auto-save means no explicit indicator; the spinner inside the toggle's disabled state communicates pending).

- [ ] **Step 2: Rewrite `frontend/tests/unit/components/JobPriorityUI.test.tsx`**

Replace all spinbutton + save-button assertions with toggle + auto-save assertions. Full rewrite of the relevant `describe` block:

```tsx
describe("JobDetailShell — priority section", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  it("admin sees Priority row in metadata", () => {
    renderShell(makeJob());
    expect(screen.getAllByText(/priority/i).length).toBeGreaterThan(0);
  });

  it("non-admin does not see Priority row", () => {
    authState.role = "user";
    renderShell(makeJob({ status: "succeeded" }));
    expect(screen.queryAllByText(/priority/i)).toHaveLength(0);
  });

  it("shows toggle for queued_backend status", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.getByRole("button", { name: /normal/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /priority/i }),
    ).toBeInTheDocument();
  });

  it("shows read-only badge for non-queued_backend status (priority=1)", () => {
    renderShell(makeJob({ status: "running", priority: 1 }));
    expect(screen.queryByRole("button", { name: /normal/i })).toBeNull();
    expect(screen.getByText(/priority/i)).toBeInTheDocument();
  });

  it("shows read-only Normal text for non-queued_backend status (priority=0)", () => {
    renderShell(makeJob({ status: "running", priority: 0 }));
    expect(screen.queryByRole("button", { name: /normal/i })).toBeNull();
    expect(screen.getByText(/normal/i)).toBeInTheDocument();
  });

  it("auto-saves on toggle without a separate Save button", async () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /priority/i }));
    await waitFor(() => {
      expect(patchMutate).toHaveBeenCalledWith({
        id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        priority: 1,
      });
    });
  });

  it("shows warning when Priority is active", async () => {
    renderShell(makeJob({ status: "queued_backend", priority: 1 }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("does not show warning when Normal is active", () => {
    renderShell(makeJob({ status: "queued_backend", priority: 0 }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
```

Note: the existing `patchMutate` mock signature was `(args, options)`. With auto-save we don't pass `onSuccess`, so the assertion drops the second argument.

- [ ] **Step 3: Run test**

```bash
cd frontend && pnpm test JobPriorityUI
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/jobs/JobDetailShell.tsx \
        frontend/tests/unit/components/JobPriorityUI.test.tsx
git commit -m "refactor(detail): rewrite PriorityEditor as PriorityToggle with auto-save

For queued_backend jobs, the toggle replaces the number Input + Save
button — auto-save on click, disabled state covers in-flight pending.
For other statuses, render a read-only badge / text. The patch mutation
signature drops the unused onSuccess callback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task B4: Replace jobs-list `PriorityCell` with badge + Popover

**Files:**

- Modify: `frontend/src/routes/_authed.jobs._index.tsx`

- [ ] **Step 1: Verify shadcn Popover is already available**

```bash
ls frontend/src/components/ui/popover.tsx
```

Expected: file exists.

- [ ] **Step 2: Rewrite `PriorityCell` (lines ~31–83)**

```tsx
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";
import { PriorityToggle } from "@/components/forms/PriorityToggle";

function PriorityCell({ job }: { job: JobSummary }) {
  const { t } = useTranslation();
  const patch = usePatchJob();
  const canEdit = job.status === "queued_backend";
  const current = (job.priority ?? 0) === 0 ? 0 : 1;

  if (!canEdit) {
    if (
      job.status === "running" ||
      job.status === "succeeded" ||
      job.status === "failed" ||
      job.status === "cancelled"
    ) {
      return <span className="text-muted-foreground text-xs">—</span>;
    }
    return <PriorityBadge value={current} t={t} />;
  }

  function onChange(next: 0 | 1) {
    if (next === current) return;
    patch.mutate({ id: job.id, priority: next });
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("jobs.priority.label")}
          className="cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-full"
        >
          <PriorityBadge value={current} t={t} />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-2" align="start">
        <PriorityToggle
          value={current}
          onChange={onChange}
          disabled={patch.isPending}
          size="sm"
        />
      </PopoverContent>
    </Popover>
  );
}

function PriorityBadge({
  value,
  t,
}: {
  value: 0 | 1;
  t: (k: string) => string;
}) {
  if (value === 1) {
    return (
      <Badge
        variant="outline"
        className="bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-300"
      >
        ⚡ {t("jobs.priority.high")}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground">
      {t("jobs.priority.normal")}
    </Badge>
  );
}
```

- [ ] **Step 3: Add a test for the popover-open path**

Append to `JobPriorityUI.test.tsx` (after the existing `JobsListPage` describe):

```tsx
import userEvent from "@testing-library/user-event";

describe("JobsListPage — priority cell popover", () => {
  beforeEach(() => {
    authState.role = "admin";
    vi.clearAllMocks();
  });

  // Reuse the listing page render helper from earlier in the file. If the helper
  // does not seed a queued_backend job by default, extend it locally.
  it("clicking a priority badge for queued_backend opens a popover with the toggle", async () => {
    // assumes renderListPage seeds at least one queued_backend job; otherwise
    // mock useJobs to return one and re-render here.
    renderListPage();
    const badges = screen.getAllByRole("button", { name: /priority/i });
    await userEvent.click(badges[0]);
    expect(
      await screen.findByRole("button", { name: /normal/i }),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run test**

```bash
cd frontend && pnpm test JobPriorityUI
```

Expected: PASS. If the popover's pointer-events shim is missing for jsdom (per `.../memory/project_radix_pointer_events_testing.md`), the test setup file `frontend/tests/setup.ts` should already have it — verify by re-running. If it fails specifically on `pointerEvents`, see that memory entry's shim.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/_authed.jobs._index.tsx \
        frontend/tests/unit/components/JobPriorityUI.test.tsx
git commit -m "refactor(jobs-list): replace inline number cell with badge + Popover toggle

Tight column space gets a colored chip (amber for ⚡ Priority, muted for
Normal); clicking opens a shadcn Popover hosting the same PriorityToggle
component used in submit-form and detail. Non-editable statuses (running
/ terminal) show '—' to communicate priority is locked once dispatched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task B5: Final i18n keys (zh-TW + en) + drop dead keys

**Files:**

- Modify: `frontend/src/i18n/zh-TW.json`
- Modify: `frontend/src/i18n/en.json`

- [ ] **Step 1: Update `zh-TW.json` `jobs.priority` and `jobs.help.priority_admin`**

```diff
   "jobs": {
     "priority": {
       "label": "優先度",
+      "normal": "正常",
+      "high": "優先",
       "column": "優先度",
       "adminOnly": "僅管理員",
-      "editPlaceholder": "0",
-      "save": "儲存優先度",
       "warning": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。"
     },
     "help": {
       "test_dataset_optional": "...",
-      "priority_admin": "提高優先度會暫停較低優先度工作的提交，直到此工作被派送至 Volcano 為止。正在執行中的工作不受影響。",
+      "priority_admin": "Priority 排在 Normal 工作前面；多個 Priority 工作之間照送出時間排（先送先跑）。Running 工作不受影響。",
       "source_model": "..."
     }
   }
```

- [ ] **Step 2: Update `en.json` mirror**

```diff
   "jobs": {
     "priority": {
       "label": "Priority",
+      "normal": "Normal",
+      "high": "Priority",
       "column": "Priority",
       "adminOnly": "Admin only",
-      "editPlaceholder": "0",
-      "save": "Save priority",
       "warning": "Bumping priority pauses submission of new lower-priority jobs to Volcano until this job is dispatched. Running jobs are not affected."
     },
     "help": {
       "test_dataset_optional": "...",
-      "priority_admin": "Raising priority halts dispatch of lower-priority queued jobs until this one reaches Volcano. Already-running jobs are unaffected.",
+      "priority_admin": "Priority jobs run before Normal jobs; among Priority jobs, oldest first. Running jobs are unaffected.",
       "source_model": "..."
     }
   }
```

- [ ] **Step 3: Confirm prettier likes both JSON files**

```bash
pnpm dlx prettier --check frontend/src/i18n/zh-TW.json frontend/src/i18n/en.json
```

If fails, run `--write`.

- [ ] **Step 4: Run frontend tests once more (i18n picks up immediately)**

```bash
cd frontend && pnpm test
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/i18n/zh-TW.json frontend/src/i18n/en.json
git commit -m "i18n(jobs): finalize priority keys — add normal/high, drop editPlaceholder/save

Updates the help-text to describe α (fixed-tier FIFO-within-tier) instead
of the integer-bump phrasing. zh-TW + en kept in lockstep.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task B6: Smoke test the priority UX in the dev server

**Files:** none (manual verification)

- [ ] **Step 1: Start dev server**

```bash
cd frontend && pnpm dev
```

- [ ] **Step 2: In a browser, log in as admin (or set `AUTH_DEV_EMAIL` to an admin email)**

- [ ] **Step 3: Click "New job" → fill required fields → confirm two-button toggle is visible in the Priority card**

- [ ] **Step 4: Toggle to ⚡ Priority → confirm warning text appears**

- [ ] **Step 5: Submit a train job → in jobs list, confirm new row shows the ⚡ Priority badge while it is `queued_backend`**

- [ ] **Step 6: Click the badge in the jobs list → confirm Popover opens with the toggle**

- [ ] **Step 7: Open the job detail → confirm the same toggle is inline; click Normal → confirm auto-save (network request fires, no Save button)**

- [ ] **Step 8: Stop dev server (Ctrl-C)**

If any step fails, fix the underlying issue and re-test before moving on; do not proceed to Section C with a broken Q2.

---

## Section C — Q1 Hyperparameters typed widgets

### Task C1: Add Radix slider + switch packages

**Files:**

- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`

- [ ] **Step 1: Install packages**

```bash
cd frontend
pnpm add @radix-ui/react-slider @radix-ui/react-switch
```

- [ ] **Step 2: Verify packages added**

```bash
grep -E "@radix-ui/react-slider|@radix-ui/react-switch" frontend/package.json
```

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "chore(deps): add @radix-ui/react-slider + @radix-ui/react-switch

Backing primitives for the new shadcn slider + switch (next tasks).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C2: Create shadcn `Slider` primitive

**Files:**

- Create: `frontend/src/components/ui/slider.tsx`

- [ ] **Step 1: Write the file**

`frontend/src/components/ui/slider.tsx` (canonical shadcn template — verbatim from the upstream registry):

```tsx
import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

import { cn } from "@/lib/utils";

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn(
      "relative flex w-full touch-none select-none items-center",
      className,
    )}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-secondary">
      <SliderPrimitive.Range className="absolute h-full bg-primary" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb className="block h-5 w-5 rounded-full border-2 border-primary bg-background ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50" />
  </SliderPrimitive.Root>
));
Slider.displayName = SliderPrimitive.Root.displayName;

export { Slider };
```

- [ ] **Step 2: Verify TS compiles**

```bash
cd frontend && pnpm typecheck
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/slider.tsx
git commit -m "feat(ui): add shadcn Slider primitive (manual to avoid CLI artifact conflicts)

Backing widget for the RangeSliderWidget — RJSF widget for bounded floats.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C3: Create shadcn `Switch` primitive

**Files:**

- Create: `frontend/src/components/ui/switch.tsx`

- [ ] **Step 1: Write the file**

```tsx
import * as React from "react";
import * as SwitchPrimitives from "@radix-ui/react-switch";

import { cn } from "@/lib/utils";

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  React.ComponentPropsWithoutRef<typeof SwitchPrimitives.Root>
>(({ className, ...props }, ref) => (
  <SwitchPrimitives.Root
    className={cn(
      "peer inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      className,
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitives.Thumb
      className={cn(
        "pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0",
      )}
    />
  </SwitchPrimitives.Root>
));
Switch.displayName = SwitchPrimitives.Root.displayName;

export { Switch };
```

- [ ] **Step 2: Verify**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui/switch.tsx
git commit -m "feat(ui): add shadcn Switch primitive (manual)

Backing widget for the SwitchWidget — RJSF widget for booleans, replacing
the default checkbox rendering.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C4: Create `RangeSliderWidget` (TDD)

**Files:**

- Create: `frontend/src/components/forms/widgets/RangeSliderWidget.tsx`
- Test: `frontend/tests/unit/widgets/RangeSliderWidget.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { RangeSliderWidget } from "@/components/forms/widgets/RangeSliderWidget";

const baseProps = {
  id: "test_id",
  label: "test",
  schema: { type: "number", minimum: 0, maximum: 1, default: 0.5 } as const,
  uiSchema: {},
  options: {},
  formContext: {},
  registry: {} as never, // RJSF runtime injects this; widgets we author rarely use it
  onBlur: () => {},
  onFocus: () => {},
  required: false,
  disabled: false,
  readonly: false,
  rawErrors: [] as string[],
  multiple: false,
  hideError: false,
};

describe("RangeSliderWidget", () => {
  it("renders a slider and a numeric input that share the value", () => {
    render(
      <RangeSliderWidget {...baseProps} value={0.7} onChange={() => {}} />,
    );
    const numInput = screen.getByRole("spinbutton");
    expect(numInput).toHaveValue(0.7);
    // Radix Slider renders role=slider on the thumb
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuenow", "0.7");
  });

  it("calls onChange when the numeric input changes", async () => {
    const onChange = vi.fn();
    render(
      <RangeSliderWidget {...baseProps} value={0.5} onChange={onChange} />,
    );
    const numInput = screen.getByRole("spinbutton");
    await userEvent.clear(numInput);
    await userEvent.type(numInput, "0.3");
    expect(onChange).toHaveBeenLastCalledWith(0.3);
  });

  it("respects schema minimum / maximum on the slider", () => {
    render(
      <RangeSliderWidget {...baseProps} value={0.5} onChange={() => {}} />,
    );
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("aria-valuemin", "0");
    expect(slider).toHaveAttribute("aria-valuemax", "1");
  });
});
```

- [ ] **Step 2: Run — verify fails**

```bash
cd frontend && pnpm test RangeSliderWidget
```

Expected: FAIL "Cannot find module".

- [ ] **Step 3: Implement**

```tsx
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import type { WidgetProps } from "@rjsf/utils";

const STEP_DEFAULT = 0.01;

export function RangeSliderWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : 0;
  const max = typeof schema.maximum === "number" ? schema.maximum : 1;
  const step =
    typeof schema.multipleOf === "number" ? schema.multipleOf : STEP_DEFAULT;

  const numeric = typeof value === "number" ? value : Number(value ?? min);

  function handleNumeric(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value === "" ? null : Number(e.target.value);
    onChange(v === null || Number.isNaN(v) ? undefined : v);
  }

  function handleSlider([next]: number[]) {
    onChange(next);
  }

  return (
    <div className="flex items-center gap-3">
      <Slider
        value={[numeric]}
        min={min}
        max={max}
        step={step}
        onValueChange={handleSlider}
        disabled={disabled || readonly}
        className="flex-1"
      />
      <Input
        id={id}
        type="number"
        value={Number.isNaN(numeric) ? "" : numeric}
        onChange={handleNumeric}
        min={min}
        max={max}
        step={step}
        disabled={disabled || readonly}
        className="w-20 font-mono text-sm"
      />
    </div>
  );
}
```

- [ ] **Step 4: Run — verify passes**

```bash
cd frontend && pnpm test RangeSliderWidget
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/widgets/RangeSliderWidget.tsx \
        frontend/tests/unit/widgets/RangeSliderWidget.test.tsx
git commit -m "feat(widgets): add RangeSliderWidget — slider + numeric input combo for bounded floats

Two-way bound: slider thumb and numeric input both edit the same value.
Step defaults to schema.multipleOf, falling back to 0.01.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C5: Create `StepperWidget` (TDD)

**Files:**

- Create: `frontend/src/components/forms/widgets/StepperWidget.tsx`
- Test: `frontend/tests/unit/widgets/StepperWidget.test.tsx`

- [ ] **Step 1: Test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { StepperWidget } from "@/components/forms/widgets/StepperWidget";

const baseProps = {
  id: "n",
  label: "n",
  schema: { type: "integer", minimum: 1, default: 100 } as const,
  uiSchema: {},
  options: {},
  formContext: {},
  registry: {} as never,
  onBlur: () => {},
  onFocus: () => {},
  required: false,
  disabled: false,
  readonly: false,
  rawErrors: [] as string[],
  multiple: false,
  hideError: false,
};

describe("StepperWidget", () => {
  it("renders − value + buttons and an input", () => {
    render(<StepperWidget {...baseProps} value={100} onChange={() => {}} />);
    expect(
      screen.getByRole("button", { name: /decrement/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /increment/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("spinbutton")).toHaveValue(100);
  });

  it("increments by 1 on + click", async () => {
    const onChange = vi.fn();
    render(<StepperWidget {...baseProps} value={100} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /increment/i }));
    expect(onChange).toHaveBeenCalledWith(101);
  });

  it("decrements by 1 on − click but never below schema.minimum", async () => {
    const onChange = vi.fn();
    render(<StepperWidget {...baseProps} value={1} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /decrement/i }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Verify failing**

```bash
cd frontend && pnpm test StepperWidget
```

- [ ] **Step 3: Implement**

```tsx
import { Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { WidgetProps } from "@rjsf/utils";

export function StepperWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : -Infinity;
  const max = typeof schema.maximum === "number" ? schema.maximum : Infinity;
  const step = typeof schema.multipleOf === "number" ? schema.multipleOf : 1;

  const numeric = typeof value === "number" ? value : Number(value ?? min);

  function bump(delta: number) {
    const next = numeric + delta;
    if (next < min || next > max) return;
    onChange(next);
  }

  function handleInput(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value === "" ? null : Number(e.target.value);
    onChange(v === null || Number.isNaN(v) ? undefined : v);
  }

  const cantDec = numeric - step < min;
  const cantInc = numeric + step > max;

  return (
    <div className="inline-flex items-center rounded-md border bg-background">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => bump(-step)}
        disabled={disabled || readonly || cantDec}
        aria-label="decrement"
        className="h-8 w-8 rounded-r-none p-0"
      >
        <Minus className="h-4 w-4" />
      </Button>
      <Input
        id={id}
        type="number"
        value={Number.isNaN(numeric) ? "" : numeric}
        onChange={handleInput}
        min={isFinite(min) ? min : undefined}
        max={isFinite(max) ? max : undefined}
        step={step}
        disabled={disabled || readonly}
        className="h-8 w-20 rounded-none border-x text-center font-mono text-sm"
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => bump(step)}
        disabled={disabled || readonly || cantInc}
        aria-label="increment"
        className="h-8 w-8 rounded-l-none p-0"
      >
        <Plus className="h-4 w-4" />
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Verify passing**

```bash
cd frontend && pnpm test StepperWidget
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/widgets/StepperWidget.tsx \
        frontend/tests/unit/widgets/StepperWidget.test.tsx
git commit -m "feat(widgets): add StepperWidget — − value + buttons for integer fields

Respects schema.minimum/maximum; step defaults to schema.multipleOf || 1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C6: Create `NumericInputWidget`

**Files:**

- Create: `frontend/src/components/forms/widgets/NumericInputWidget.tsx`

- [ ] **Step 1: Implement (no separate test file — covered transitively by RjsfConfigForm tests)**

```tsx
import { Input } from "@/components/ui/input";
import type { WidgetProps } from "@rjsf/utils";

export function NumericInputWidget(props: WidgetProps) {
  const { value, onChange, schema, disabled, readonly, id } = props;
  const min = typeof schema.minimum === "number" ? schema.minimum : undefined;
  const max = typeof schema.maximum === "number" ? schema.maximum : undefined;
  const step =
    typeof schema.multipleOf === "number" ? schema.multipleOf : "any";

  return (
    <Input
      id={id}
      type="number"
      value={value === undefined || value === null ? "" : value}
      onChange={(e) => {
        const v = e.target.value === "" ? null : Number(e.target.value);
        onChange(v === null || Number.isNaN(v) ? undefined : v);
      }}
      min={min}
      max={max}
      step={step}
      disabled={disabled || readonly}
      className="font-mono text-sm"
    />
  );
}
```

- [ ] **Step 2: TS compile check**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/forms/widgets/NumericInputWidget.tsx
git commit -m "feat(widgets): add NumericInputWidget — single number input for unbounded floats / one-sided bounds

Used when a number schema lacks both minimum and maximum (e.g. lr: float
gt=0). Type=number, mono font for visual consistency with the slider /
stepper widgets.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C7: Create `SwitchWidget` (TDD)

**Files:**

- Create: `frontend/src/components/forms/widgets/SwitchWidget.tsx`
- Test: `frontend/tests/unit/widgets/SwitchWidget.test.tsx`

- [ ] **Step 1: Test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { SwitchWidget } from "@/components/forms/widgets/SwitchWidget";

const baseProps = {
  id: "x",
  label: "x",
  schema: { type: "boolean", default: false } as const,
  uiSchema: {},
  options: {},
  formContext: {},
  registry: {} as never,
  onBlur: () => {},
  onFocus: () => {},
  required: false,
  disabled: false,
  readonly: false,
  rawErrors: [] as string[],
  multiple: false,
  hideError: false,
};

describe("SwitchWidget", () => {
  it("renders a switch in the value state", () => {
    render(<SwitchWidget {...baseProps} value={false} onChange={() => {}} />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("toggles via click", async () => {
    const onChange = vi.fn();
    render(<SwitchWidget {...baseProps} value={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
```

- [ ] **Step 2: Verify failing**

```bash
cd frontend && pnpm test SwitchWidget
```

- [ ] **Step 3: Implement**

```tsx
import { Switch } from "@/components/ui/switch";
import type { WidgetProps } from "@rjsf/utils";

export function SwitchWidget(props: WidgetProps) {
  const { value, onChange, disabled, readonly, id } = props;
  return (
    <Switch
      id={id}
      checked={!!value}
      onCheckedChange={(c) => onChange(c)}
      disabled={disabled || readonly}
    />
  );
}
```

- [ ] **Step 4: Verify passing**

```bash
cd frontend && pnpm test SwitchWidget
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/widgets/SwitchWidget.tsx \
        frontend/tests/unit/widgets/SwitchWidget.test.tsx
git commit -m "feat(widgets): add SwitchWidget — shadcn Switch wrapper for boolean fields

Replaces RJSF's default CheckboxWidget for type=boolean schemas.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C8: Create `FieldTemplate` (label row with default badge + per-field reset)

**Files:**

- Create: `frontend/src/components/forms/templates/FieldTemplate.tsx`
- Test: `frontend/tests/unit/widgets/FieldTemplate.test.tsx`

- [ ] **Step 1: Test**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { FieldTemplate } from "@/components/forms/templates/FieldTemplate";

const baseProps = {
  id: "f",
  classNames: "",
  label: "field_x",
  required: false,
  disabled: false,
  readonly: false,
  errors: <></>,
  help: <></>,
  description: <p className="text-muted-foreground">desc</p>,
  rawDescription: "desc",
  rawHelp: "",
  rawErrors: [] as string[],
  schema: { type: "number", default: 0.5 } as const,
  uiSchema: {},
  formContext: {},
  registry: {} as never,
  hidden: false,
  displayLabel: true,
};

describe("FieldTemplate", () => {
  it("shows 'default 0.5' badge when value === default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.5}>
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText(/default 0\.5/i)).toBeInTheDocument();
    expect(screen.queryByText(/modified/i)).toBeNull();
  });

  it("shows 'modified' badge when value !== default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.7}>
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText(/modified/i)).toBeInTheDocument();
  });

  it("renders a reset button when value !== default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.7}>
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByRole("button", { name: /reset/i })).toBeInTheDocument();
  });

  it("does not render reset button when value === default", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.5}>
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.queryByRole("button", { name: /reset/i })).toBeNull();
  });

  it("calls formContext.onResetField with the field id when reset is clicked", async () => {
    const onResetField = vi.fn();
    render(
      <FieldTemplate
        {...baseProps}
        formData={0.7}
        formContext={{ onResetField }}
      >
        <input value="0.7" readOnly />
      </FieldTemplate>,
    );
    await userEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(onResetField).toHaveBeenCalledWith("f");
  });

  it("renders the description below the control", () => {
    render(
      <FieldTemplate {...baseProps} formData={0.5}>
        <input value="0.5" readOnly />
      </FieldTemplate>,
    );
    expect(screen.getByText("desc")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Verify failing**

```bash
cd frontend && pnpm test FieldTemplate
```

- [ ] **Step 3: Implement**

```tsx
import { RotateCcw } from "lucide-react";
import type { FieldTemplateProps } from "@rjsf/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

interface ResetCtx {
  onResetField?: (id: string) => void;
}

export function FieldTemplate(props: FieldTemplateProps) {
  const {
    id,
    classNames,
    label,
    required,
    children,
    description,
    schema,
    formData,
    displayLabel,
    formContext,
  } = props;

  const ctx = (formContext ?? {}) as ResetCtx;
  const defaultValue = (schema as { default?: unknown }).default;
  const isModified = defaultValue !== undefined && formData !== defaultValue;
  const showReset = isModified && typeof ctx.onResetField === "function";

  return (
    <div className={`mb-4 ${classNames ?? ""}`}>
      {displayLabel && (
        <div className="mb-1 flex items-center gap-2">
          <Label htmlFor={id} className="font-medium">
            {label}
            {required && <span className="ml-1 text-destructive">*</span>}
          </Label>
          {defaultValue !== undefined && !isModified && (
            <Badge
              variant="outline"
              className="text-muted-foreground text-xs font-normal"
            >
              default {JSON.stringify(defaultValue)}
            </Badge>
          )}
          {isModified && (
            <Badge variant="default" className="text-xs font-normal">
              modified
            </Badge>
          )}
          {showReset && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => ctx.onResetField!(id)}
              className="ml-auto h-6 px-2 text-xs text-muted-foreground"
            >
              <RotateCcw className="mr-1 h-3 w-3" />
              reset
            </Button>
          )}
        </div>
      )}
      <div>{children}</div>
      {description}
    </div>
  );
}
```

- [ ] **Step 4: Verify passing**

```bash
cd frontend && pnpm test FieldTemplate
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/forms/templates/FieldTemplate.tsx \
        frontend/tests/unit/widgets/FieldTemplate.test.tsx
git commit -m "feat(forms): add FieldTemplate — label row with default badge + per-field reset

Renders the field name + a context-aware badge ('default X' when value
matches default, 'modified' otherwise). When modified and the form
context exposes onResetField(), a small reset button reverts just this
field to its default. Description renders below the control unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C9: Wire widgets + template into `RjsfConfigForm`

**Files:**

- Modify: `frontend/src/components/forms/RjsfConfigForm.tsx`
- Modify: `frontend/src/components/forms/RjsfConfigForm.logic.ts`

- [ ] **Step 1: Update `RjsfConfigForm.logic.ts` — type→widget rule**

Replace the `walk` function with:

```ts
function walk(node: StrictRJSFSchema, ui: UiSchema): void {
  const { properties } = node;
  if (!properties) return;
  const entries = Object.entries(properties) as [
    string,
    StrictRJSFSchema | boolean,
  ][];
  for (const [k, child] of entries) {
    if (typeof child === "boolean") continue;
    const childUi: UiSchema = (ui[k] as UiSchema) ?? {};

    // Type → widget mapping. Selected widgets are registered in
    // RjsfConfigForm.tsx's `widgets` prop.
    const isNumber = child.type === "number";
    const isInteger = child.type === "integer";
    const isBoolean = child.type === "boolean";
    const hasMin = typeof child.minimum === "number";
    const hasMax = typeof child.maximum === "number";

    if (isNumber && hasMin && hasMax) {
      childUi["ui:widget"] = "rangeSlider";
    } else if (isInteger) {
      childUi["ui:widget"] = "stepper";
    } else if (isNumber) {
      childUi["ui:widget"] = "numericInput";
    } else if (isBoolean) {
      childUi["ui:widget"] = "switch";
    }
    // string + enum → default SelectWidget (RJSF picks it automatically)

    walk(child, childUi);
    if (Object.keys(childUi).length > 0) ui[k] = childUi;
  }
}
```

Drop the previous `ui:placeholder` line — defaults are now communicated via the `FieldTemplate` badge, so the placeholder is redundant.

- [ ] **Step 2: Update `RjsfConfigForm.tsx` — pass `widgets` + `templates` + reset handler**

Replace the body of `RjsfConfigForm` with:

```tsx
import Form from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import { useCallback, useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { deriveUiSchemaFromSchema, fillDefaults } from "./RjsfConfigForm.logic";
import { FieldTemplate } from "./templates/FieldTemplate";
import { RangeSliderWidget } from "./widgets/RangeSliderWidget";
import { StepperWidget } from "./widgets/StepperWidget";
import { NumericInputWidget } from "./widgets/NumericInputWidget";
import { SwitchWidget } from "./widgets/SwitchWidget";

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
  for (const [k, v] of Object.entries(node)) out[k] = normalizeSchema(v);
  if (typeof out.$ref === "string") {
    const { $ref, ...rest } = out;
    const hasSiblings = Object.keys(rest).some(
      (k) => !NON_WRAPPING_SIBLINGS.has(k),
    );
    if (hasSiblings) return { allOf: [{ $ref }], ...rest };
  }
  return out;
}

const widgets = {
  rangeSlider: RangeSliderWidget,
  stepper: StepperWidget,
  numericInput: NumericInputWidget,
  switch: SwitchWidget,
};

const templates = { FieldTemplate };

export function RjsfConfigForm({ schema, value, onChange }: Props) {
  const normalizedSchema = useMemo(
    () => normalizeSchema(schema) as RJSFSchema,
    [schema],
  );
  const uiSchema = useMemo(
    () => deriveUiSchemaFromSchema(normalizedSchema),
    [normalizedSchema],
  );
  const defaults = useMemo(
    () => fillDefaults(normalizedSchema, {}),
    [normalizedSchema],
  );

  useEffect(() => {
    onChange(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to schema changes
  }, [normalizedSchema]);

  const onResetField = useCallback(
    (fieldId: string) => {
      // RJSF builds field ids as `root_<key>` (configurable via idPrefix).
      const key = fieldId.replace(/^root_/, "");
      const next = {
        ...value,
        [key]: (defaults as Record<string, unknown>)[key],
      };
      onChange(next);
    },
    [value, defaults, onChange],
  );

  return (
    <div className="rjsf-wrap rounded-md border bg-card p-4 text-sm">
      <Form
        schema={normalizedSchema}
        uiSchema={uiSchema}
        validator={validator}
        formData={value}
        widgets={widgets}
        templates={templates}
        formContext={{ onResetField }}
        liveValidate
        showErrorList={false}
        onChange={(e) => onChange(e.formData as Record<string, unknown>)}
      >
        <div className="mt-4 flex justify-end">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onChange(defaults)}
          >
            Reset all to defaults
          </Button>
        </div>
      </Form>
    </div>
  );
}
```

- [ ] **Step 3: Run tests**

```bash
cd frontend && pnpm test RjsfConfigForm
```

Expected: PASS (existing tests may need small adjustments — see Task C10).

- [ ] **Step 4: Commit (defer to Task C10 for atomic test-update commit)**

Skip — combine with Task C10 to keep test-update + wiring in one commit when fixes are needed. If existing tests pass without change, commit now:

```bash
git add frontend/src/components/forms/RjsfConfigForm.tsx \
        frontend/src/components/forms/RjsfConfigForm.logic.ts
git commit -m "feat(forms): wire RjsfConfigForm to type-aware widgets + FieldTemplate

deriveUiSchemaFromSchema picks rangeSlider / stepper / numericInput /
switch based on schema type + bounds; widgets prop maps these names to
the new widget components. formContext.onResetField wires per-field
reset back to the parent's onChange.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

### Task C10: Update existing `RjsfConfigForm` tests

**Files:**

- Modify: `frontend/tests/unit/RjsfConfigForm.test.tsx`
- Modify: `frontend/tests/unit/RjsfConfigForm.logic.test.ts`

- [ ] **Step 1: Update logic test to assert new uiSchema rules**

`frontend/tests/unit/RjsfConfigForm.logic.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { deriveUiSchemaFromSchema } from "@/components/forms/RjsfConfigForm.logic";

describe("deriveUiSchemaFromSchema", () => {
  it("maps bounded float (min+max) to rangeSlider", () => {
    const ui = deriveUiSchemaFromSchema({
      type: "object",
      properties: { t: { type: "number", minimum: 0, maximum: 1 } },
    });
    expect((ui.t as Record<string, unknown>)["ui:widget"]).toBe("rangeSlider");
  });

  it("maps integer to stepper", () => {
    const ui = deriveUiSchemaFromSchema({
      type: "object",
      properties: { n: { type: "integer", minimum: 1 } },
    });
    expect((ui.n as Record<string, unknown>)["ui:widget"]).toBe("stepper");
  });

  it("maps unbounded float to numericInput", () => {
    const ui = deriveUiSchemaFromSchema({
      type: "object",
      properties: { lr: { type: "number", exclusiveMinimum: 0 } },
    });
    expect((ui.lr as Record<string, unknown>)["ui:widget"]).toBe(
      "numericInput",
    );
  });

  it("maps boolean to switch", () => {
    const ui = deriveUiSchemaFromSchema({
      type: "object",
      properties: { flag: { type: "boolean" } },
    });
    expect((ui.flag as Record<string, unknown>)["ui:widget"]).toBe("switch");
  });

  it("does not set ui:widget for string with enum", () => {
    const ui = deriveUiSchemaFromSchema({
      type: "object",
      properties: { mode: { type: "string", enum: ["a", "b"] } },
    });
    expect(
      (ui.mode as Record<string, unknown> | undefined)?.["ui:widget"],
    ).toBeUndefined();
  });
});
```

- [ ] **Step 2: Update integration test in `RjsfConfigForm.test.tsx`**

The existing test asserts default-placeholder behavior. Since defaults are now shown via badge (FieldTemplate), update assertions:

```tsx
it("renders typed widgets for each field type", () => {
  const schema = {
    type: "object",
    properties: {
      threshold: { type: "number", minimum: 0, maximum: 1, default: 0.5 },
      n_estimators: { type: "integer", minimum: 1, default: 100 },
      flag: { type: "boolean", default: false },
    },
  };
  render(<RjsfConfigForm schema={schema} value={{}} onChange={() => {}} />);
  // bounded float → slider role
  expect(screen.getByRole("slider")).toBeInTheDocument();
  // integer → ± buttons
  expect(
    screen.getByRole("button", { name: /increment/i }),
  ).toBeInTheDocument();
  // boolean → switch role
  expect(screen.getByRole("switch")).toBeInTheDocument();
});

it("shows 'default X' badge per field initially", () => {
  const schema = {
    type: "object",
    properties: {
      threshold: { type: "number", minimum: 0, maximum: 1, default: 0.5 },
    },
  };
  render(
    <RjsfConfigForm
      schema={schema}
      value={{ threshold: 0.5 }}
      onChange={() => {}}
    />,
  );
  expect(screen.getByText(/default 0\.5/i)).toBeInTheDocument();
});
```

Remove/replace any test that asserted the old `ui:placeholder` "Default: ..." string — that lookup will no longer exist.

- [ ] **Step 3: Run all RjsfConfigForm tests**

```bash
cd frontend && pnpm test RjsfConfigForm
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/unit/RjsfConfigForm.test.tsx \
        frontend/tests/unit/RjsfConfigForm.logic.test.ts
git commit -m "test(forms): update RjsfConfigForm tests for type-aware widget rendering

Assertions now check for slider/spinbutton/switch roles instead of the
generic <input>. Logic tests cover the new type → ui:widget mapping
(rangeSlider / stepper / numericInput / switch / default-Select).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Section D — Final integration + PR

### Task D1: Full test suite + typecheck + lint

- [ ] **Step 1: Run vitest**

```bash
cd frontend && pnpm test
```

Expected: all PASS.

- [ ] **Step 2: Run typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: no errors.

- [ ] **Step 3: Run lint + format check**

```bash
cd frontend && pnpm lint && pnpm format:check
```

Expected: clean. If format fails, run `pnpm format`.

- [ ] **Step 4: Run pre-commit on all repo files (catches the docs files)**

```bash
pre-commit run --all-files
```

Expected: PASS.

### Task D2: Manual smoke (full end-to-end)

- [ ] **Step 1: Start dev server**

```bash
cd frontend && pnpm dev
```

- [ ] **Step 2: As admin, submit a `train` job for elf-rf**

Confirm:

- Hyperparameters block shows StepperWidget for `n_estimators` (− 100 +) and `random_state`, NumericInputWidget for `max_depth` (it's `int | None` — nullable; if RJSF default rendering kicks in for the null toggle, that's acceptable for v3 — note in PR description if so).
- Each field has a `default X` badge. Changing a value flips it to `modified` and shows a per-field reset.
- Bottom "Reset all to defaults" button reverts everything.

- [ ] **Step 3: Submit an `evaluate` job (against a 4.0.0 model still in MLflow)**

Hyperparameters block should still show the legacy `threshold` slider (RangeSliderWidget) — confirms the widget pipeline picks it up correctly. Field is inert per spec §6.4 (legacy 4.0.0 manifest unchanged).

- [ ] **Step 4: Toggle Priority on the same submit form, verify warning appears**

- [ ] **Step 5: Stop dev server**

### Task D3: Open PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin feat/submit-job-v3-priority-hparams-stance
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "feat(submit-job): v3 — priority toggle + typed hparam widgets + platform stance docs" \
  --body "$(cat <<'EOF'
## Summary

Continues the v0.20.x submit-job hardening line. Three concerns in one PR:

- **Q1 Hyperparameters typed widgets**: RJSF widget remap to slider+input combo (bounded float), stepper (int), switch (bool). Per-field 'default X' / 'modified' badge + per-field reset. Generic `Input` for unbounded numeric.
- **Q2 Priority toggle**: Replace admin number input with a Normal · ⚡ Priority two-button group. Fixed mapping {0, 1}. All three sites (submit form / detail page / jobs list) converge on the same component. Detail + jobs-list auto-save; jobs-list uses badge + Popover for column-width fit.
- **Q4 Platform stance**: `docs/architecture.md` §1.1/§1.2/§1.3 (glue / deploy-not-dev / stage-aware UX). `CLAUDE.md` Hard rule. `docs/detector-repos.md` table bumped to 4.1.0.

Backend untouched.

Spec: `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md`
Plan: `docs/superpowers/plans/2026-05-08-submit-job-ux-and-platform-stance.md`

## Companion PRs (cross-repo threshold eradication)

- maldet `chore/remove-evaluateconfig-threshold-template`
- elfrfdet `chore/remove-evaluateconfig-threshold`
- elfcnndet `chore/remove-evaluateconfig-threshold`

## Test plan

- [x] `pnpm test` — all unit tests green (PriorityToggle, RangeSliderWidget, StepperWidget, SwitchWidget, FieldTemplate, JobPriorityUI, RjsfConfigForm)
- [x] `pnpm typecheck` clean
- [x] `pnpm format:check` + `pnpm lint` clean
- [x] `pre-commit run --all-files` clean
- [x] Manual smoke: train submit shows correct widgets per field type; priority toggle works in all three sites; auto-save fires patch on detail + jobs-list

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Return PR URL to the user**

---

## Self-review against spec

Spec coverage check (against `2026-05-08-submit-job-priority-hparams-threshold-design.md`):

- §4.1–§4.6 Q1 Hyperparameters → Tasks C1–C10 (radix deps, shadcn primitives, 4 widgets, FieldTemplate, RjsfConfigForm wire-up, test updates)
- §5.1–§5.5 Q2 Priority → Tasks B1–B6 (PriorityToggle, three sites, i18n, smoke)
- §10.1 architecture.md §1 → Task A1
- §10.2 CLAUDE.md Hard rule → Task A2
- §10.3 detector-repos.md table → Task A3
- §10.4 operator-process implications → no implementation; reviewers cite the new sections going forward

No gaps. Spec items not in this plan: Q3 (threshold removal in detector + maldet repos) — covered by the companion plan `2026-05-08-threshold-eradication-cross-repo.md`.

Type / signature consistency check:

- `PriorityToggle` props `value: 0 | 1`, `onChange: (next: 0 | 1) => void` — used identically in B2/B3/B4
- RJSF widget names `rangeSlider` / `stepper` / `numericInput` / `switch` — used identically in C9 (logic) and C9 (component widgets map)
- `formContext.onResetField` signature `(id: string) => void` — defined in C9, asserted in C8 test, called from FieldTemplate
