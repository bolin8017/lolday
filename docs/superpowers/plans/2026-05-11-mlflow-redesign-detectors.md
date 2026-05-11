# MLflow Redesign — Detector Repos Bump Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bump `elfrfdet` and `elfcnndet` to pull maldet 2.2.1 — exercises the new MLflow event routing, MLflow Models flavor save/load, and dataset lineage automatically. **Detector source code does NOT need changes** because the MLflow integration is entirely inside maldet.

**Architecture:** Minimal bump — `pyproject.toml` `maldet>=2.2,<3.0`, `maldet.toml` `compat.min_maldet = "2.2"`, version bump for both detectors, CHANGELOG entry. Lolday's build pipeline rebuilds the Docker images on the next `POST /detectors/{name}/builds` from a fresh git tag; image push to Harbor is handled by the existing platform, not this plan.

**Tech Stack:** Python 3.12, hatchling, uv (build/test). Tests are pytest unit suites; no infrastructure beyond `uv run pytest`.

**Reference:** Spec — `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`. Depends on **Plan A** (maldet 2.2.1 published to PyPI).

---

## File Structure

### elfrfdet — `/home/bolin8017/Documents/repositories/elfrfdet`

| Path             | Change                                                                  |
| ---------------- | ----------------------------------------------------------------------- |
| `pyproject.toml` | bump `version` 4.1.0 → 4.2.0; bump `maldet[mlflow]` pin to `>=2.2,<3.0` |
| `maldet.toml`    | bump `[compat] min_maldet = "2.2"`                                      |
| `CHANGELOG.md`   | add `[4.2.0] — 2026-05-11` section                                      |
| `uv.lock`        | regenerate via `uv sync`                                                |

### elfcnndet — `/home/bolin8017/Documents/repositories/elfcnndet`

| Path             | Change                                                                            |
| ---------------- | --------------------------------------------------------------------------------- |
| `pyproject.toml` | bump `version` 4.1.0 → 4.2.0; bump `maldet[lightning,mlflow]` pin to `>=2.2,<3.0` |
| `maldet.toml`    | bump `[compat] min_maldet = "2.2"`                                                |
| `CHANGELOG.md`   | add `[4.2.0] — 2026-05-11` section                                                |
| `uv.lock`        | regenerate via `uv sync`                                                          |

### lolday (live smoke) — `/home/bolin8017/Documents/repositories/lolday`

| Path                                        | Change                                                        |
| ------------------------------------------- | ------------------------------------------------------------- |
| `tests/2026-05-11-mlflow-redesign-smoke.sh` | New shell smoke test exercising the full pipeline on server30 |

---

## Pre-condition check

- [ ] **Confirm maldet 2.2.1 is on PyPI before starting this plan.**

```bash
curl -s https://pypi.org/pypi/maldet/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
```

Expected: `2.2.1`. If it prints `2.1.0` or `2.2.0`, finish Plan A first.

---

## Task 1: Bump elfrfdet

**Files:**

- Modify: `/home/bolin8017/Documents/repositories/elfrfdet/pyproject.toml`
- Modify: `/home/bolin8017/Documents/repositories/elfrfdet/maldet.toml`
- Modify: `/home/bolin8017/Documents/repositories/elfrfdet/CHANGELOG.md`

- [ ] **Step 1: Bump version and dependency pin**

Edit `pyproject.toml`:

```diff
- version = "4.1.0"
+ version = "4.2.0"
- description = "Random Forest ELF malware detector — reference template for the lolday platform on the maldet 2.0 framework"
+ description = "Random Forest ELF malware detector — reference template for the lolday platform on the maldet 2.2 framework"
- "maldet[mlflow]>=2.0,<3.0",
+ "maldet[mlflow]>=2.2.1,<3.0",
```

- [ ] **Step 2: Bump min_maldet in manifest**

Edit `maldet.toml`:

```diff
- min_maldet = "2.0"
+ min_maldet = "2.2"
```

- [ ] **Step 3: Add CHANGELOG entry**

Prepend to `CHANGELOG.md` (under the title, before `## [4.1.0]`):

```markdown
## [4.2.0] - 2026-05-11

### Changed

- Bumped `maldet[mlflow]` pin to `>=2.2,<3.0` to pick up the new MLflow data-model integration:
  - `confusion_matrix.json` and `per_class_metrics.json` as proper artifacts (no more stringified Python repr in tags)
  - `warnings.jsonl` artifact aggregating per-sample feature-extractor failures (no more tag overwrites)
  - MLflow Models flavor — trained models now ship with `MLmodel` YAML, `python_env.yaml`, and an inferred signature
  - Dataset lineage via `mlflow.log_input` per stage
- Bumped `[compat] min_maldet = "2.2"`.
- No source-code changes in this repo — all behavior changes flow from the maldet bump.

### Migration

- Lolday users: this detector version requires lolday running maldet-2.2-aware backend (post 2026-05-11). Older lolday backends will warn but proceed.
- Models trained with elfrfdet 4.1.0 (raw `model.joblib`) are NOT loadable by elfrfdet 4.2.0 trainers (which expect MLflow Models layout). To migrate, retrain the model on the new version. The 4.1.0 detector image stays in lolday's Detector Version listing for legacy evaluate/predict against existing models per lolday's 2-week grace policy.
```

- [ ] **Step 4: Refresh uv lock and run tests**

```bash
cd /home/bolin8017/Documents/repositories/elfrfdet
uv sync --reinstall-package maldet
uv run pytest -x
```

Expected: tests pass (existing tests don't exercise MLflow directly, so the bump should be invisible to them).

- [ ] **Step 5: Commit and tag**

```bash
cd /home/bolin8017/Documents/repositories/elfrfdet
git add pyproject.toml maldet.toml CHANGELOG.md uv.lock
git commit -m "chore: bump to 4.2.0 — maldet 2.2 (MLflow data-model redesign)"
git tag -a v4.2.0 -m "elfrfdet 4.2.0"
git push origin main --tags
```

---

## Task 2: Bump elfcnndet

**Files:**

- Modify: `/home/bolin8017/Documents/repositories/elfcnndet/pyproject.toml`
- Modify: `/home/bolin8017/Documents/repositories/elfcnndet/maldet.toml`
- Modify: `/home/bolin8017/Documents/repositories/elfcnndet/CHANGELOG.md`

- [ ] **Step 1: Bump version and dependency pin**

Edit `pyproject.toml`:

```diff
- version = "4.1.0"
+ version = "4.2.0"
- "maldet[lightning,mlflow]>=2.0,<3.0",
+ "maldet[lightning,mlflow]>=2.2.1,<3.0",
```

Update the description line analogously to elfrfdet ("maldet 2.0 framework" → "maldet 2.2 framework").

- [ ] **Step 2: Bump min_maldet**

Edit `maldet.toml`:

```diff
- min_maldet = "2.0"
+ min_maldet = "2.2"
```

- [ ] **Step 3: Add CHANGELOG entry**

Same template as Task 1 Step 3 — adjust title to `elfcnndet 4.2.0` and the Lightning-flavored migration note:

```markdown
- Models trained with elfcnndet 4.1.0 (raw `model.ckpt`) are NOT loadable by elfcnndet 4.2.0 trainers (which expect MLflow Models layout). LightningTrainer.load no longer requires `model_factory=` — `mlflow.pytorch.load_model` handles class reconstruction.
```

- [ ] **Step 4: Refresh uv lock and run tests**

```bash
cd /home/bolin8017/Documents/repositories/elfcnndet
uv sync --reinstall-package maldet
uv run pytest -x
```

Expected: green.

- [ ] **Step 5: Commit and tag**

```bash
cd /home/bolin8017/Documents/repositories/elfcnndet
git add pyproject.toml maldet.toml CHANGELOG.md uv.lock
git commit -m "chore: bump to 4.2.0 — maldet 2.2 (MLflow data-model redesign)"
git tag -a v4.2.0 -m "elfcnndet 4.2.0"
git push origin main --tags
```

---

## Task 3: Trigger lolday build for each detector

> **Pre-requisite**: lolday backend + frontend deployed with Plan B changes; base image `pytorch-cu12-base:v5+` pushed to Harbor (Plan B Task 11).

- [ ] **Step 1: Login to lolday UI and submit a build for each detector**

Go to lolday → Detectors → `elf-rf` → "Build new version" → enter git tag `v4.2.0`. Same for `elf-cnn`.

- [ ] **Step 2: Verify build completion**

```bash
# Check build status
kubectl get pods -n lolday -l app.kubernetes.io/component=build -w
```

Wait until both builds reach `Succeeded`. Verify in lolday UI that two new `DetectorVersion` rows appear (`elf-rf/v4.2.0`, `elf-cnn/v4.2.0`).

- [ ] **Step 3: Verify `maldet_version` was captured**

```bash
kubectl exec -n lolday deploy/postgres -- psql -U lolday -d lolday -c \
  "SELECT name, git_tag, maldet_version FROM detector d JOIN detector_version dv ON d.id=dv.detector_id WHERE dv.git_tag='v4.2.0';"
```

Expected: both rows show `maldet_version = '2.2'` (from manifest compat floor — see Plan B Task 6's pragmatic v1 fallback).

---

## Task 4: End-to-end smoke test

**Files:**

- Create: `/home/bolin8017/Documents/repositories/lolday/tests/2026-05-11-mlflow-redesign-smoke.sh`

- [ ] **Step 1: Write the smoke script**

Create the file:

```bash
#!/usr/bin/env bash
# Live smoke test for the 2026-05-11 MLflow data-model redesign.
#
# Pre-requisites:
#   - maldet 2.2.1 on PyPI
#   - elfrfdet 4.2.0 + elfcnndet 4.2.0 image pushed to Harbor
#   - lolday backend deployed with Plan B changes
#   - base image pytorch-cu12-base:v5+
#
# Run from any host with kubectl access to the lolday K3s cluster.

set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
MLFLOW_URL="${MLFLOW_URL:-http://mlflow.lolday.svc.cluster.local:5000}"

assert() {
    local desc="$1"
    local expected="$2"
    local actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "✓ $desc"
    else
        echo "✗ $desc: expected '$expected', got '$actual'"
        exit 1
    fi
}

# Helper: curl MLflow via ephemeral pod
mlflow_curl() {
    local suffix
    suffix="$(date +%s)-$$"
    kubectl run -n "$NAMESPACE" "mlflow-smoke-${suffix}" --rm -i --restart=Never \
        --image=curlimages/curl:8.10.1 --quiet -- "$@"
}

echo "==> Step 1: Submit a train run via lolday API"
# (uses local JWT — operator runs this after authenticating via /auth/cf-access)
JOB_RESPONSE="$(curl -s -X POST "$LOLDAY_URL/jobs" \
    -H "Cookie: $LOLDAY_COOKIE" -H "Content-Type: application/json" \
    -d '{"detector_version_id":"'"$ELF_RF_42_DV_ID"'","type":"train","train_dataset_id":"'"$TRAIN_DS_ID"'"}')"
RUN_ID="$(echo "$JOB_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['mlflow_run_id'])")"
JOB_ID="$(echo "$JOB_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")"
echo "  job_id=$JOB_ID  mlflow_run_id=$RUN_ID"

echo "==> Step 2: Wait for job to reach SUCCEEDED (timeout 600s)"
SECONDS=0
while (( SECONDS < 600 )); do
    STATUS="$(curl -s "$LOLDAY_URL/jobs/$JOB_ID" -H "Cookie: $LOLDAY_COOKIE" \
        | python -c "import sys,json; print(json.load(sys.stdin)['status'])")"
    if [[ "$STATUS" == "succeeded" ]]; then break; fi
    if [[ "$STATUS" == "failed" || "$STATUS" == "timeout" ]]; then
        echo "✗ job ended with status=$STATUS"; exit 1
    fi
    sleep 10
done

echo "==> Step 3: Validate MLflow run state"
RUN_JSON="$(mlflow_curl -s "$MLFLOW_URL/api/2.0/mlflow/runs/get?run_id=$RUN_ID")"

START_TIME="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['start_time'])")"
END_TIME="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['end_time'])")"
STATUS="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['status'])")"

[[ "$START_TIME" != "0" ]] || { echo "✗ start_time is 0"; exit 1; }
[[ "$END_TIME" != "0" ]] || { echo "✗ end_time is 0"; exit 1; }
assert "run.status" "FINISHED" "$STATUS"

echo "==> Step 4: Validate provenance tags"
TAGS="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.dumps({t['key']: t['value'] for t in json.load(sys.stdin)['run']['data']['tags']}))")"

for k in mlflow.source.git.commit lolday.detector_image_digest lolday.maldet_version \
         lolday.train_dataset_id lolday.resource_profile lolday.gpu_count; do
    VAL="$(echo "$TAGS" | python -c "import sys,json,os; print(json.load(sys.stdin).get(os.environ['K'],''))" K="$k")"
    [[ -n "$VAL" ]] || { echo "✗ tag $k missing"; exit 1; }
    echo "  ✓ tag $k = $VAL"
done

echo "==> Step 5: Validate artifacts (train run)"
ART="$(mlflow_curl -s "$MLFLOW_URL/api/2.0/mlflow/artifacts/list?run_id=$RUN_ID")"
echo "$ART" | python -c "import sys,json; files=[f['path'] for f in json.load(sys.stdin).get('files',[])]; assert 'model' in files, files; print('  ✓ model/ artifact present')"

MODEL_LISTING="$(mlflow_curl -s "$MLFLOW_URL/api/2.0/mlflow/artifacts/list?run_id=$RUN_ID&path=model")"
echo "$MODEL_LISTING" | python -c "import sys,json; files=[f['path'] for f in json.load(sys.stdin).get('files',[])]; assert any('MLmodel' in f for f in files), files; print('  ✓ MLmodel YAML present')"

echo "==> Step 6: Validate system metrics"
METRICS="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.dumps({m['key']: m['value'] for m in json.load(sys.stdin)['run']['data']['metrics']}))")"
for k in system/cpu_utilization_percentage system/system_memory_usage_megabytes; do
    HAS="$(echo "$METRICS" | python -c "import sys,json,os; d=json.load(sys.stdin); print('yes' if os.environ['K'] in d else 'no')" K="$k")"
    [[ "$HAS" == "yes" ]] || { echo "✗ metric $k missing"; exit 1; }
    echo "  ✓ metric $k present"
done

echo "==> Step 7: Negative test — kill a vcjob mid-run, verify reconciler finalizes MLflow"
NEG_JOB_RESPONSE="$(curl -s -X POST "$LOLDAY_URL/jobs" \
    -H "Cookie: $LOLDAY_COOKIE" -H "Content-Type: application/json" \
    -d '{"detector_version_id":"'"$ELF_RF_42_DV_ID"'","type":"train","train_dataset_id":"'"$TRAIN_DS_ID"'"}')"
NEG_RUN_ID="$(echo "$NEG_JOB_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['mlflow_run_id'])")"
NEG_JOB_ID="$(echo "$NEG_JOB_RESPONSE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")"

# Wait until pod is RUNNING
sleep 30
kubectl delete vcjob -n "$NAMESPACE" "train-${NEG_JOB_ID//-/}" --wait=false || true

# Wait for reconciler (period ~10s) + finalize
sleep 30

NEG_STATUS="$(mlflow_curl -s "$MLFLOW_URL/api/2.0/mlflow/runs/get?run_id=$NEG_RUN_ID" \
    | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['status'])")"
assert "killed-job MLflow run status" "FAILED" "$NEG_STATUS"

echo "==> All checks passed ✓"
```

- [ ] **Step 2: Chmod + commit**

```bash
cd /home/bolin8017/Documents/repositories/lolday
chmod +x tests/2026-05-11-mlflow-redesign-smoke.sh
git add tests/2026-05-11-mlflow-redesign-smoke.sh
git commit -m "test: live smoke for MLflow data-model redesign"
```

- [ ] **Step 3: Run with operator's JWT cookie**

The operator runs:

```bash
export LOLDAY_URL="https://lolday.connlabai.com"
export LOLDAY_COOKIE="..."          # from browser devtools
export ELF_RF_42_DV_ID="..."        # the new 4.2.0 DetectorVersion UUID
export TRAIN_DS_ID="..."            # any existing train dataset
bash tests/2026-05-11-mlflow-redesign-smoke.sh
```

Expected: all 7 numbered checks pass.

---

## Self-review

- **Spec coverage**: Plan C's only obligation is consuming Plan As published maldet 2.2.1 and Plan B's lolday changes. The smoke script (Task 4) validates the full spec §8.3 acceptance criteria including the negative orphan-run test.
- **Type consistency**: this plan only bumps pin strings — no type interactions.
- **No placeholders**: every step has concrete code or commands. The smoke script's `LOLDAY_COOKIE` is a runtime credential the operator supplies; not a placeholder in the planning sense.
