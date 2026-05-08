# Threshold Eradication (Cross-repo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eradicate `EvaluateConfig.threshold` (a #112-pattern footgun: declared, validated, surfaced in UI, never plumbed through). Remove from elfrfdet, elfcnndet, and the maldet scaffolding templates so the field never appears in any new manifest. `BinaryClassification.evaluate()` already uses `model.predict()` — no maldet evaluator change needed.

**Architecture:** Three independent PRs in three repos. maldet PR can land first to keep templates clean for future scaffolding; the two detector PRs can be parallel. After merge, operator tags releases and runs `POST /detectors/<id>/builds` so Harbor + lolday DB get the 4.1.0 image and detector-version row.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, Jinja2 (maldet templates).

**Spec:** `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md` §6 + §7

---

## File structure

### maldet repo (`~/Documents/repositories/maldet`)

- Modify: `src/maldet/templates/rf/src/configs.py.j2`
- Modify: `src/maldet/templates/cnn/src/configs.py.j2`
- Modify: `tests/fixtures/sample_configs.py`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` (version bump)

### elfrfdet repo (`~/Documents/repositories/elfrfdet`)

- Modify: `src/elfrfdet/configs.py`
- Modify: `tests/test_configs.py`
- Modify: `maldet.toml`
- Modify: `pyproject.toml` (mirror version)
- Modify: `CHANGELOG.md`

### elfcnndet repo (`~/Documents/repositories/elfcnndet`)

- Modify: `src/elfcnndet/configs.py`
- Modify: `tests/test_configs.py`
- Modify: `maldet.toml`
- Modify: `pyproject.toml` (mirror version)
- Modify: `CHANGELOG.md`

---

## Section A — maldet templates cleanup

### Task A1: Branch + remove `threshold` from `rf` template

**Files:**

- Modify: `~/Documents/repositories/maldet/src/maldet/templates/rf/src/configs.py.j2`

- [ ] **Step 1: Create branch**

```bash
cd ~/Documents/repositories/maldet
git checkout main && git pull
git checkout -b chore/remove-evaluateconfig-threshold-template
```

- [ ] **Step 2: Read current template to confirm exact text**

```bash
grep -n "threshold" ~/Documents/repositories/maldet/src/maldet/templates/rf/src/configs.py.j2
```

Expected: line ~22 shows `threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Decision threshold.")`

- [ ] **Step 3: Remove the `threshold` line from `EvaluateConfig` in the rf template**

Use Edit to delete only the `threshold: float = Field(...)` line. If `EvaluateConfig` becomes empty, leave the class header + a `pass` body so Pydantic v2 still produces a valid (empty) JSON schema.

After:

```jinja
class EvaluateConfig(_Strict):
    pass
```

- [ ] **Step 4: Confirm only the threshold line was removed**

```bash
git diff src/maldet/templates/rf/src/configs.py.j2
```

Expected: single deletion of the `threshold:` line and (if the class became empty) addition of `pass`.

### Task A2: Remove `threshold` from `cnn` template

**Files:**

- Modify: `~/Documents/repositories/maldet/src/maldet/templates/cnn/src/configs.py.j2`

- [ ] **Step 1: Read current cnn template**

```bash
grep -n "threshold" ~/Documents/repositories/maldet/src/maldet/templates/cnn/src/configs.py.j2
```

Expected: line ~26 shows `threshold: float = Field(default=0.5, ge=0.0, le=1.0)`.

- [ ] **Step 2: Remove the `threshold` line, mirror Task A1**

After: `EvaluateConfig` ends with `pass` if no other fields remain.

- [ ] **Step 3: Confirm**

```bash
git diff src/maldet/templates/cnn/src/configs.py.j2
```

### Task A3: Update fixture + CHANGELOG + version bump

**Files:**

- Modify: `~/Documents/repositories/maldet/tests/fixtures/sample_configs.py`
- Modify: `~/Documents/repositories/maldet/CHANGELOG.md`
- Modify: `~/Documents/repositories/maldet/pyproject.toml`

- [ ] **Step 1: Remove the `threshold` line from the fixture**

```bash
grep -n "threshold" ~/Documents/repositories/maldet/tests/fixtures/sample_configs.py
```

Expected: line 24 shows `threshold: float = 0.5`. Remove that line.

- [ ] **Step 2: Bump version in `pyproject.toml`**

```bash
grep -n "^version" ~/Documents/repositories/maldet/pyproject.toml
```

Bump minor: e.g. `2.0.0` → `2.1.0` (read current value then increment minor).

- [ ] **Step 3: Add CHANGELOG entry**

Top of `CHANGELOG.md` (after the title), add:

```markdown
## 2.1.0 — 2026-05-08

### Removed

- `threshold` field from binary-classification scaffolding templates (`templates/rf/src/configs.py.j2`, `templates/cnn/src/configs.py.j2`) and from the `tests/fixtures/sample_configs.py` fixture. Scaffolds generated from these templates no longer carry the field. Existing detector repos that scaffolded earlier should remove `EvaluateConfig.threshold` themselves on their next version bump (see lolday spec `2026-05-08-submit-job-priority-hparams-threshold-design.md` §6 for rationale).

### Notes

- `BinaryClassification.evaluate()` is unchanged; it has always called `model.predict()` (default 0.5 argmax) and ignored any `threshold` field declared in stage configs. The removal eliminates a leaky-abstraction footgun where users could believe they were tuning the operating point when they were not.
```

- [ ] **Step 4: Run full test suite to confirm nothing else broke**

```bash
cd ~/Documents/repositories/maldet
uv sync --all-extras
uv run pytest -q
```

Expected: all tests pass (the removed fixture key was only referenced by tests of the fixture itself, if any; verify no FAIL).

If any test fails because it referenced the threshold fixture key: update that test too as part of this task (do not bypass).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/repositories/maldet
git add src/maldet/templates/rf/src/configs.py.j2 \
        src/maldet/templates/cnn/src/configs.py.j2 \
        tests/fixtures/sample_configs.py \
        pyproject.toml \
        CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore(templates): remove EvaluateConfig.threshold from rf+cnn scaffolds

The field was declared by the templates, validated to [0.0, 1.0], but never
plumbed through to BinaryClassification.evaluate() — a #112-pattern footgun
where users could believe they were tuning the operating point when they
were not. Remove from templates so future scaffolds inherit the clean shape.

Existing detector repos (elfrfdet, elfcnndet) remove the field themselves
on their next version bump; tracked in lolday spec
2026-05-08-submit-job-priority-hparams-threshold-design.md.

Bump 2.0.0 -> 2.1.0 (minor; additive removal at the framework level).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push branch + open PR**

```bash
cd ~/Documents/repositories/maldet
git push -u origin chore/remove-evaluateconfig-threshold-template
gh pr create --title "chore(templates): remove EvaluateConfig.threshold from rf+cnn scaffolds" \
  --body "$(cat <<'EOF'
## Summary

- Remove the `threshold: float = Field(...)` line from both binary-classification scaffolding templates and the test fixture.
- Bump 2.0.0 → 2.1.0 (CHANGELOG entry included).

## Why

`threshold` was declared by the templates but never used by `BinaryClassification.evaluate()`. Same #112-pattern footgun as the detector-version override toggle. Full reasoning in the lolday spec linked below.

Spec: `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md` §6.2

## Test plan

- [x] `uv run pytest -q` passes
- [ ] Reviewer regenerates a fresh scaffold via `maldet scaffold rf foo` and confirms the new `EvaluateConfig` has no `threshold`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Section B — elfrfdet cleanup

### Task B1: Branch + remove `threshold` from `EvaluateConfig`

**Files:**

- Modify: `~/Documents/repositories/elfrfdet/src/elfrfdet/configs.py`
- Modify: `~/Documents/repositories/elfrfdet/tests/test_configs.py`

- [ ] **Step 1: Create branch**

```bash
cd ~/Documents/repositories/elfrfdet
git checkout main && git pull
git checkout -b chore/remove-evaluateconfig-threshold
```

- [ ] **Step 2: Edit `src/elfrfdet/configs.py` — remove threshold from `EvaluateConfig`**

Current `configs.py:25-26` has:

```python
class EvaluateConfig(_Strict):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Decision threshold.")
```

After:

```python
class EvaluateConfig(_Strict):
    pass
```

- [ ] **Step 3: Remove the threshold range test from `tests/test_configs.py`**

Find and delete the entire `test_evaluate_config_threshold_range` function (lines around 28–34). Its imports of `EvaluateConfig` may still be needed by other tests; check before deleting any imports.

- [ ] **Step 4: Run tests to confirm everything still passes**

```bash
cd ~/Documents/repositories/elfrfdet
uv sync --all-extras
uv run pytest -q
```

Expected: PASS, no FAIL. The threshold-range test is gone and no remaining test references the field.

### Task B2: Bump `maldet.toml` + `pyproject.toml` + CHANGELOG + commit

**Files:**

- Modify: `~/Documents/repositories/elfrfdet/maldet.toml`
- Modify: `~/Documents/repositories/elfrfdet/pyproject.toml`
- Modify: `~/Documents/repositories/elfrfdet/CHANGELOG.md`

- [ ] **Step 1: Bump detector version in `maldet.toml`**

```bash
grep -n "^version" ~/Documents/repositories/elfrfdet/maldet.toml
```

Change `version = "4.0.0"` (or current) → `version = "4.1.0"`.

- [ ] **Step 2: Bump `pyproject.toml` version to match**

Mirror the same `4.0.0` → `4.1.0` bump.

- [ ] **Step 3: Add CHANGELOG entry**

Top of `CHANGELOG.md`:

```markdown
## 4.1.0 — 2026-05-08

### Removed

- `EvaluateConfig.threshold` field. The field was declared with default 0.5, range [0.0, 1.0], but `maldet.evaluators.binary.BinaryClassification.evaluate()` never used it — `model.predict()` was called directly (= sklearn argmax 0.5). The schema-declared knob silently had no effect on metrics. Removed to match the actual evaluator behavior. If a non-0.5 operating point is needed in the future, bake it into the trained model artifact (see scikit-learn `TunedThresholdClassifierCV`) or implement a custom `Evaluator` protocol implementation in this repo.

### Migration

- New training jobs against this version produce model artifacts that work on lolday with no behavioral change.
- Lolday uses 4.0.0 for any model trained before this bump (manifest lookup is by `detector_version_id`); the legacy schema still surfaces the inert `threshold` field for evaluations on those models. Lolday spec `2026-05-08-submit-job-priority-hparams-threshold-design.md` §6.4 specifies a 2-week grace period before retiring 4.0.0.
```

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/repositories/elfrfdet
git add src/elfrfdet/configs.py tests/test_configs.py maldet.toml pyproject.toml CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore(config): remove EvaluateConfig.threshold (footgun) + bump 4.1.0

The threshold field was declared in EvaluateConfig with range [0.0, 1.0]
but maldet.evaluators.binary.BinaryClassification.evaluate() never
consumed it — model.predict() was called directly (= sklearn argmax
0.5). UI users could change the value with no observable effect on
reported metrics. Same #112-pattern leaky abstraction.

If a non-0.5 operating point is ever needed: bake it into the trained
artifact via TunedThresholdClassifierCV at training time, or implement
a custom Evaluator protocol implementation in this repo. The lolday
platform deliberately does not expose evaluate-time tuning knobs (see
lolday spec 2026-05-08-... §1.2 + §1.3).

Bump 4.0.0 -> 4.1.0 (minor; field had no behavior so removal is not
operationally breaking).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Push branch + open PR**

```bash
cd ~/Documents/repositories/elfrfdet
git push -u origin chore/remove-evaluateconfig-threshold
gh pr create --title "chore(config): remove EvaluateConfig.threshold (footgun) + bump 4.1.0" \
  --body "$(cat <<'EOF'
## Summary

- Remove `EvaluateConfig.threshold` field (declared but never plumbed through to the evaluator).
- Bump 4.0.0 → 4.1.0 in `maldet.toml` + `pyproject.toml`.
- CHANGELOG entry with migration notes.

## Why

`maldet.evaluators.binary.BinaryClassification.evaluate()` calls `model.predict()` directly — the `threshold` field has been silently ignored since day one. Users could believe they were tuning the operating point. Same #112-pattern footgun.

Spec: `docs/superpowers/specs/2026-05-08-submit-job-priority-hparams-threshold-design.md` §6

## Test plan

- [x] `uv run pytest -q` passes
- [ ] After merge, operator tags `4.1.0` and runs `POST /detectors/<id>/builds` from a lolday admin session; confirms image lands in Harbor and the new detector_version row appears in the lolday Detectors UI

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Section C — elfcnndet cleanup

Mirror of Section B against the elfcnndet repo.

### Task C1: Branch + remove `threshold` from `EvaluateConfig`

**Files:**

- Modify: `~/Documents/repositories/elfcnndet/src/elfcnndet/configs.py`
- Modify: `~/Documents/repositories/elfcnndet/tests/test_configs.py`

- [ ] **Step 1: Create branch**

```bash
cd ~/Documents/repositories/elfcnndet
git checkout main && git pull
git checkout -b chore/remove-evaluateconfig-threshold
```

- [ ] **Step 2: Edit `src/elfcnndet/configs.py` line 27–28**

Current:

```python
class EvaluateConfig(_Strict):
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Decision threshold.")
```

After:

```python
class EvaluateConfig(_Strict):
    pass
```

- [ ] **Step 3: Remove `test_evaluate_config_threshold_range` from `tests/test_configs.py`**

Lines around 44–50.

- [ ] **Step 4: Run tests**

```bash
cd ~/Documents/repositories/elfcnndet
uv sync --all-extras
uv run pytest -q
```

Expected: PASS.

### Task C2: Bump version + CHANGELOG + commit + PR

**Files:**

- Modify: `~/Documents/repositories/elfcnndet/maldet.toml`
- Modify: `~/Documents/repositories/elfcnndet/pyproject.toml`
- Modify: `~/Documents/repositories/elfcnndet/CHANGELOG.md`

- [ ] **Step 1: Bump versions to 4.1.0**

Same as Task B2 step 1+2.

- [ ] **Step 2: Add CHANGELOG entry**

Same content as Task B2 step 3, replace "elfrfdet" references with "elfcnndet" if any (the migration text is detector-agnostic; no edits needed).

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/repositories/elfcnndet
git add src/elfcnndet/configs.py tests/test_configs.py maldet.toml pyproject.toml CHANGELOG.md
git commit -m "$(cat <<'EOF'
chore(config): remove EvaluateConfig.threshold (footgun) + bump 4.1.0

[Same body as elfrfdet PR — adjust detector name only if needed.]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push + PR**

```bash
cd ~/Documents/repositories/elfcnndet
git push -u origin chore/remove-evaluateconfig-threshold
gh pr create --title "chore(config): remove EvaluateConfig.threshold (footgun) + bump 4.1.0" \
  --body "[Same body as elfrfdet PR.]"
```

---

## Section D — Operator runtime sequence (post-merge)

Run only after Sections A–C PRs merge.

### Task D1: Tag detectors + push

- [ ] **Step 1: Tag and push elfrfdet 4.1.0**

```bash
cd ~/Documents/repositories/elfrfdet
git checkout main && git pull
git tag 4.1.0
git push origin 4.1.0
```

- [ ] **Step 2: Tag and push elfcnndet 4.1.0**

```bash
cd ~/Documents/repositories/elfcnndet
git checkout main && git pull
git tag 4.1.0
git push origin 4.1.0
```

### Task D2: Build images via lolday

For each detector, run `POST /detectors/<id>/builds` against the new tag.

- [ ] **Step 1: Find detector IDs**

In a lolday admin browser session, open Detectors page; copy the UUID for elfrfdet and elfcnndet.

Or via curl (need an admin Cloudflare Access JWT cookie):

```bash
curl -s "https://<lolday-host>/api/v1/detectors" \
  -H "Cookie: CF_Authorization=<jwt>" | jq '.[] | {id, display_name}'
```

- [ ] **Step 2: Trigger build for elfrfdet**

```bash
curl -X POST "https://<lolday-host>/api/v1/detectors/<elfrfdet-id>/builds" \
  -H "Cookie: CF_Authorization=<jwt>" \
  -H "Content-Type: application/json" \
  -d '{"git_tag": "4.1.0"}'
```

Expected: 200 OK with the new build row JSON.

- [ ] **Step 3: Trigger build for elfcnndet**

Same as Step 2 with the elfcnndet UUID.

- [ ] **Step 4: Wait for builds + verify images**

Poll the Detectors page or:

```bash
curl -s "https://<lolday-host>/api/v1/detectors/<id>/versions" \
  -H "Cookie: CF_Authorization=<jwt>" | jq '.[] | {git_tag, status}'
```

Expected: a `4.1.0` row with `status=active` for each detector. Build duration: typically 5–15 min per detector (BuildKit + Harbor push).

### Task D3: Verify the field is gone in submit-job UI

- [ ] **Step 1: Open lolday UI as admin, click "New job"**

- [ ] **Step 2: Pick `train` job type, select elf-rf detector and version `4.1.0`**

Expected: Hyperparameters block shows `n_estimators`, `max_depth`, `random_state` — **no `threshold` field**.

- [ ] **Step 3: Switch to `evaluate` type, pick a model trained on 4.1.0**

If no model exists yet on 4.1.0, train one first against `4.1.0` using a tiny dataset, then evaluate it.

Expected: Hyperparameters block is empty (EvaluateConfig has no fields) — **no `threshold`** anywhere.

- [ ] **Step 4: Verify legacy 4.0.0 still works (regression check)**

Pick a model trained on 4.0.0 (if one exists) and create an evaluate job. The form will still show `threshold` (legacy 4.0.0 manifest unchanged) but the value continues to have no effect — same as before this PR. No regression.

### Task D4: Schedule 4.0.0 retirement (calendar reminder)

- [ ] **Step 1: Set calendar reminder for 2026-05-22 (today + 14 days)**

When the date arrives, mark elfrfdet 4.0.0 and elfcnndet 4.0.0 detector versions as `inactive` in the lolday DB (operator action; SQL or admin endpoint per `docs/runbooks/admin-priority.md` follow-up — not specified by this plan).

This task is _scheduled future work_; do not run it as part of this implementation cycle.

---

## Self-review against spec

Spec coverage check (against `2026-05-08-submit-job-priority-hparams-threshold-design.md`):

- §6.1 Decision recap → covered by Tasks A1–C2 (template + config removals)
- §6.2 Files touched → 1:1 mapping with the file lists in this plan
- §6.3 Operator follow-up → Tasks D1–D3
- §6.4 Legacy 4.0.0 manifests → Task D4 (scheduled)
- §6.5 Mainstream alignment → no implementation needed; the alignment is justification only
- §7 Cross-repo coordination → mirrored by Sections A → B → C → D ordering

No gaps. Spec items not in this plan: Q1 (Hyperparameters UI), Q2 (Priority button), Q4 (docs codification) — those are in the companion lolday plan `2026-05-08-submit-job-ux-and-platform-stance.md`.
