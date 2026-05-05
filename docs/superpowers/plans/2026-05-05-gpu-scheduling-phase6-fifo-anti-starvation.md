# Phase 6 — GPU FIFO + Anti-Starvation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop large GPU jobs from being starved by smaller ones — drop the `lolday-jobs-quota` GPU axis (admission race) and enable Volcano's `sla` plugin in tier 1 (scheduler-level allocate iteration).

**Architecture:** Two-line chart change (delete one quota line, add one helm value). The SLA plugin enters via Volcano sub-chart's official `custom.scheduler_config_override` escape hatch — no custom hooks, no patcher Job, no new RBAC. A new shell smoke test reproduces the live-cluster Test D scenario (staggered job finish times) to assert that `d-BIG` schedules before `d-SMALL`.

**Tech Stack:** Helm 3 umbrella chart, Volcano 1.14.1 sub-chart, kubectl, bash, jq, public `nvidia/cuda` image for the smoke (no PyTorch needed — `nvidia-smi` mounts only verify GPU plumbing).

**Spec:** [`docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`](../specs/2026-05-05-gpu-fifo-anti-starvation-design.md)

---

## File Structure

**Modify:**

- `charts/lolday/templates/jobs-quota.yaml` (-1 line — drop `requests.nvidia.com/gpu`)
- `charts/lolday/values.yaml` (+~22 lines — add `volcano.custom.scheduler_config_override`)
- `docs/architecture.md` (~6 lines edited — Phase 1 / Phase 2 references in §10)
- `.claude/rules/charts-and-helm.md` (+~6 lines — note about `scheduler_config_override`)

**Create:**

- `tests/2026-05-05-phase6-fifo-smoke.sh` (new — ~140 lines)

**No backend / frontend / migration / image-build changes.**

---

## Task 1: Write the failing smoke test (TDD-first)

**Files:**

- Create: `tests/2026-05-05-phase6-fifo-smoke.sh`

**Why first:** TDD — the smoke needs to FAIL on the live (Phase-5-state) cluster, proving it actually checks the right thing. Once chart changes land in Tasks 2/3, this same smoke must PASS without modification.

- [ ] **Step 1.1: Write the smoke test**

Create `tests/2026-05-05-phase6-fifo-smoke.sh` with content:

```bash
#!/usr/bin/env bash
# Phase 6 smoke — sla plugin + no-GPU-quota together prevent the
# Test-D leapfrog (d-BIG = gpu=2, d-SMALL = gpu=1, jobs free at
# staggered times). Spec: docs/superpowers/specs/2026-05-05-gpu-fifo-
# anti-starvation-design.md §4.4 + §6.

set -euo pipefail

NS_INFRA=${NS_INFRA:-lolday}
NS_JOBS=${NS_JOBS:-lolday-jobs}
SCHED_CM=${SCHED_CM:-lolday-scheduler-configmap}
SCHED_DEPLOY=${SCHED_DEPLOY:-lolday-scheduler}
TEST_SLA_WAIT=${TEST_SLA_WAIT:-20s}
IMAGE=${IMAGE:-nvidia/cuda:12.6.3-base-ubuntu22.04}

ORIG_CONF=""
cleanup() {
  echo
  echo "[cleanup] deleting test jobs"
  kubectl -n "$NS_JOBS" delete jobs.batch.volcano.sh \
    -l lolday.test=phase6-fifo --wait=true 2>/dev/null || true
  if [ -n "$ORIG_CONF" ]; then
    echo "[cleanup] restoring scheduler config to original sla-waiting-time"
    kubectl -n "$NS_INFRA" patch cm "$SCHED_CM" --type=merge \
      -p "{\"data\":{\"volcano-scheduler.conf\":$(printf '%s' "$ORIG_CONF" | jq -R -s .)}}" \
      >/dev/null
    kubectl -n "$NS_INFRA" rollout restart deploy "$SCHED_DEPLOY" >/dev/null 2>&1 || true
    kubectl -n "$NS_INFRA" rollout status deploy "$SCHED_DEPLOY" --timeout=2m >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

submit_job() {
  local name=$1 gpu=$2 sleep_s=$3
  cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: batch.volcano.sh/v1alpha1
kind: Job
metadata:
  name: $name
  namespace: $NS_JOBS
  labels:
    lolday.test: phase6-fifo
    lolday.test-name: $name
spec:
  schedulerName: volcano
  minAvailable: 1
  queue: lolday-training
  ttlSecondsAfterFinished: 60
  tasks:
  - name: main
    replicas: 1
    policies:
    - event: TaskCompleted
      action: CompleteJob
    template:
      metadata:
        labels:
          lolday.test: phase6-fifo
          lolday.test-name: $name
      spec:
        restartPolicy: Never
        nodeSelector:
          kubernetes.io/hostname: server30
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          fsGroup: 1000
        containers:
        - name: gpu-busy
          image: $IMAGE
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh","-c"]
          args: ["sleep $sleep_s"]
          resources:
            requests: { cpu: 200m, memory: 256Mi }
            limits: { cpu: 1, memory: 1Gi, nvidia.com/gpu: $gpu }
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
EOF
}

echo "[step 1/5] scheduler config has sla plugin in tier 1"
plugins=$(kubectl -n "$NS_INFRA" get cm "$SCHED_CM" -o jsonpath='{.data.volcano-scheduler\.conf}')
if echo "$plugins" | grep -q "name: sla"; then
  echo "OK"
else
  echo "FAIL: sla plugin not in scheduler config"
  exit 1
fi

echo
echo "[step 2/5] lolday-jobs-quota has no nvidia.com/gpu axis"
if kubectl -n "$NS_JOBS" get resourcequota lolday-jobs-quota \
    -o jsonpath='{.spec.hard}' 2>/dev/null | grep -q "nvidia.com/gpu"; then
  echo "FAIL: lolday-jobs-quota still has nvidia.com/gpu"
  exit 1
fi
echo "OK"

echo
echo "[step 3/5] lower sla-waiting-time to $TEST_SLA_WAIT for fast test"
ORIG_CONF=$(kubectl -n "$NS_INFRA" get cm "$SCHED_CM" \
  -o jsonpath='{.data.volcano-scheduler\.conf}')
NEW_CONF=$(echo "$ORIG_CONF" \
  | sed -E "s/sla-waiting-time: [0-9a-z]+/sla-waiting-time: $TEST_SLA_WAIT/")
kubectl -n "$NS_INFRA" patch cm "$SCHED_CM" --type=merge \
  -p "{\"data\":{\"volcano-scheduler.conf\":$(printf '%s' "$NEW_CONF" | jq -R -s .)}}" \
  >/dev/null
kubectl -n "$NS_INFRA" rollout restart deploy "$SCHED_DEPLOY" >/dev/null
kubectl -n "$NS_INFRA" rollout status deploy "$SCHED_DEPLOY" --timeout=2m
sleep 5
echo "OK"

echo
echo "[step 4/5] Test D scenario — staggered finish, head-of-line gpu=2"
submit_job d-j1 1 30
submit_job d-j2 1 70
sleep 5
submit_job d-big 2 15
sleep 4
submit_job d-small 1 15
echo "submitted d-j1, d-j2, d-big, d-small"

echo
echo "[step 5/5] waiting up to 120s for d-BIG and d-SMALL pod startTime"
big_start=""
small_start=""
deadline=$(($(date +%s) + 120))
while [ $(date +%s) -lt $deadline ]; do
  [ -z "$big_start" ] && big_start=$(kubectl -n "$NS_JOBS" get pod d-big-main-0 \
    -o jsonpath='{.status.startTime}' 2>/dev/null || true)
  [ -z "$small_start" ] && small_start=$(kubectl -n "$NS_JOBS" get pod d-small-main-0 \
    -o jsonpath='{.status.startTime}' 2>/dev/null || true)
  if [ -n "$big_start" ] && [ -n "$small_start" ]; then break; fi
  sleep 4
done

if [ -z "$big_start" ] || [ -z "$small_start" ]; then
  echo "FAIL: missing startTime — big='$big_start' small='$small_start'"
  exit 1
fi

if [[ "$big_start" < "$small_start" ]]; then
  echo "OK: d-BIG ($big_start) scheduled before d-SMALL ($small_start) — sla worked"
else
  echo "FAIL: d-BIG ($big_start) scheduled AFTER d-SMALL ($small_start) — leapfrog still happens"
  exit 1
fi

echo
echo "=== PHASE 6 SMOKE PASSED ==="
```

- [ ] **Step 1.2: Make it executable**

```bash
chmod +x tests/2026-05-05-phase6-fifo-smoke.sh
```

- [ ] **Step 1.3: Run it on current cluster, verify it FAILS at step 1**

```bash
bash tests/2026-05-05-phase6-fifo-smoke.sh
```

Expected output (last few lines):

```
[step 1/5] scheduler config has sla plugin in tier 1
FAIL: sla plugin not in scheduler config
```

Exit code: 1. **This is the expected failure** — sla plugin won't be in the config until Task 3.

- [ ] **Step 1.4: Commit**

```bash
git add tests/2026-05-05-phase6-fifo-smoke.sh
git commit -m "$(cat <<'EOF'
test(phase6): add fifo + anti-starvation smoke

Reproduces the live-cluster Test D scenario from spec §4.4. Asserts
two pre-conditions (sla plugin in scheduler config, no GPU axis in
lolday-jobs-quota), temporarily lowers sla-waiting-time to 20s, runs
the staggered-finish leapfrog test, and asserts d-BIG schedules before
d-SMALL. Cleanup trap restores original config and deletes test jobs.

Smoke is currently expected to fail at step 1 (sla plugin not yet
configured); will pass once Phase 6 6a + 6b chart changes are
deployed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 6a — Remove GPU axis from `lolday-jobs-quota`

**Files:**

- Modify: `charts/lolday/templates/jobs-quota.yaml:16` (delete one line)

- [ ] **Step 2.1: Apply the diff**

Edit `charts/lolday/templates/jobs-quota.yaml`. Locate line 16 and delete the `requests.nvidia.com/gpu: "2"` line. Final content:

```yaml
{{/* Phase 1 — total resource cap on lolday-jobs namespace.
     Numbers from spec §7 Phase 1; nvidia.com/gpu axis removed in
     Phase 6 (2026-05-05) — Volcano queue capability is the GPU
     gatekeeper, see docs/superpowers/specs/2026-05-05-gpu-fifo-anti-
     starvation-design.md §6.1. */}}
apiVersion: v1
kind: ResourceQuota
metadata:
  name: lolday-jobs-quota
  namespace: {{ .Values.global.jobsNamespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  hard:
    requests.cpu: "8"
    requests.memory: 30Gi
    limits.cpu: "24"
    limits.memory: 50Gi
    count/pods: "16"
    count/jobs.batch: "10"
    count/jobs.batch.volcano.sh: "20"
```

- [ ] **Step 2.2: Verify with `helm template`**

```bash
helm template lolday charts/lolday \
  --set harbor.harborAdminPassword=x --set fernetKey=x \
  --set postgresql.password=x --set mlflow.dbPassword=x \
  --set monitoring.kps.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=x \
  --set monitoring.alertmanager.discord.criticalWebhookUrl=https://discord.com/api/webhooks/1/aA \
  --set monitoring.alertmanager.discord.warningWebhookUrl=https://discord.com/api/webhooks/1/aA \
  2>/dev/null \
  | awk '/^kind: ResourceQuota$/,/^---$/' | grep -c "nvidia.com/gpu"
```

Expected: `0`

If `helm template` complains about missing required values, set whatever it asks for to `x`. The output we care about is just the rendered ResourceQuota.

- [ ] **Step 2.3: Run helm-related pre-commit hooks dry**

```bash
pre-commit run --files charts/lolday/templates/jobs-quota.yaml
```

Expected: all hooks pass.

- [ ] **Step 2.4: Commit**

```bash
git add charts/lolday/templates/jobs-quota.yaml
git commit -m "$(cat <<'EOF'
feat(charts): phase 6a — drop nvidia.com/gpu from lolday-jobs-quota

Volcano queue capability is the sole GPU gatekeeper from Phase 6
onwards. Keeping the K8s ResourceQuota GPU axis interacts badly with
Volcano: pod admission can race ahead of the scheduler's ordering and
allow smaller GPU jobs to leapfrog larger ones (Test B in
docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md
§4.2). Other axes (CPU, memory, pod count, vcjob count) remain as
scheduler-agnostic runaway defenses.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 6b — Add `sla` plugin via `volcano.custom.scheduler_config_override`

**Files:**

- Modify: `charts/lolday/values.yaml:685-698` (the existing `volcano:` block — add `scheduler_config_override` key under `custom:`)

- [ ] **Step 3.1: Find the existing volcano block**

```bash
grep -n "^volcano:" charts/lolday/values.yaml
grep -n "metrics_enable: false" charts/lolday/values.yaml
```

Expected: `^volcano:` near line 685 and `metrics_enable: false` (the existing `custom.metrics_enable` setting from Phase 9.5) inside that block.

- [ ] **Step 3.2: Apply the diff**

Edit `charts/lolday/values.yaml`. After the existing `metrics_enable: false` line inside `volcano.custom`, add the `scheduler_config_override` key. The block becomes:

```yaml
volcano:
  # Controls both the subchart condition (Chart.yaml) and the Queue /
  # ServiceMonitor templates below.
  enabled: true
  custom:
    # Phase 9.5: keep scheduler / controller /metrics endpoints (defaults
    # scheduler_metrics_enable=true, controller_metrics_enable=true — our
    # servicemonitor-volcano.yaml scrapes them), but do NOT deploy the
    # subchart's bundled kube-state-metrics / Prometheus / Grafana. The
    # bundled KSM ships `volcanosh/kube-state-metrics:v2.0.0-beta` (2020-
    # era) carrying 6 Critical CVEs, and kube-prometheus-stack already
    # provides the real KSM + Prometheus + Grafana for the cluster. The
    # master `metrics_enable` flag gates those three extra templates only.
    metrics_enable: false
    # Phase 6 (2026-05-05): replace Volcano sub-chart default scheduler
    # config to add `sla` plugin in tier 1, providing aging-based
    # admission/pipeline boost so large GPU jobs are not perpetually
    # leapfrogged by smaller jobs. Spec:
    # docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md
    # §6.2 (layout); §5.3 (sla-waiting-time tuning rationale).
    #
    # Diff vs Volcano 1.14 default: tier 1 plugins gain `name: sla` with
    # `arguments.sla-waiting-time: 10m`. Tier 2 untouched. To tune the
    # waiting time, edit the value below and re-run scripts/deploy.sh —
    # the sub-chart's checksum/config annotation triggers scheduler
    # rollout automatically.
    scheduler_config_override: |
      actions: "enqueue, allocate, backfill"
      tiers:
      - plugins:
        - name: priority
        - name: gang
          enablePreemptable: false
        - name: conformance
        - name: sla
          arguments:
            sla-waiting-time: 10m
      - plugins:
        - name: overcommit
        - name: drf
          enablePreemptable: false
        - name: predicates
        - name: proportion
        - name: nodeorder
        - name: binpack
```

- [ ] **Step 3.3: Verify with `helm template` that scheduler ConfigMap renders correctly**

```bash
helm template lolday charts/lolday \
  --set harbor.harborAdminPassword=x --set fernetKey=x \
  --set postgresql.password=x --set mlflow.dbPassword=x \
  --set monitoring.kps.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=x \
  --set monitoring.alertmanager.discord.criticalWebhookUrl=https://discord.com/api/webhooks/1/aA \
  --set monitoring.alertmanager.discord.warningWebhookUrl=https://discord.com/api/webhooks/1/aA \
  2>/dev/null \
  | awk '/^# Source: lolday\/charts\/volcano\/templates\/scheduler.yaml$/,/^---$/' \
  | grep -A 2 "name: sla"
```

Expected output:

```
        - name: sla
          arguments:
            sla-waiting-time: 10m
```

If grep returns nothing, the override didn't make it into the rendered ConfigMap — re-check indentation in values.yaml.

- [ ] **Step 3.4: Run helm pre-commit hook**

```bash
pre-commit run --files charts/lolday/values.yaml
```

Expected: all hooks pass.

- [ ] **Step 3.5: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "$(cat <<'EOF'
feat(charts): phase 6b — enable Volcano sla plugin in tier 1

Use Volcano sub-chart's official `custom.scheduler_config_override`
escape hatch to replace the default scheduler config. The diff vs
upstream is one new tier-1 plugin: `sla` with `sla-waiting-time:
10m`. This gives aging-based admission/pipeline boost (per Volcano
SLA plugin design doc) so large GPU jobs that have waited > 10m
move to Inqueue and reserve the next free GPU slot, preventing
indefinite leapfrog by smaller jobs.

Spec: docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-
design.md §6.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Deploy + verify smoke passes

**Files:** none (deployment + verification only).

- [ ] **Step 4.1: Source secrets**

```bash
source .lolday-secrets.env || source ~/.lolday-secrets.env
```

If the file is missing, ask the operator. Do not invent values.

- [ ] **Step 4.2: Deploy**

```bash
bash scripts/deploy.sh
```

Expected: deploy completes, Helm release shows `STATUS: deployed`, no errors.

- [ ] **Step 4.3: Verify ResourceQuota live state**

```bash
kubectl -n lolday-jobs get resourcequota lolday-jobs-quota \
  -o jsonpath='{.spec.hard}' | python3 -m json.tool
```

Expected: no `requests.nvidia.com/gpu` key in the output.

- [ ] **Step 4.4: Verify scheduler ConfigMap live state**

```bash
kubectl -n lolday get cm lolday-scheduler-configmap \
  -o jsonpath='{.data.volcano-scheduler\.conf}' | grep -A 2 "name: sla"
```

Expected:

```
  - name: sla
    arguments:
      sla-waiting-time: 10m
```

- [ ] **Step 4.5: Verify scheduler Pod restarted with new config**

```bash
kubectl -n lolday get pods -l app=volcano-scheduler \
  -o custom-columns=NAME:.metadata.name,AGE:.metadata.creationTimestamp,READY:.status.containerStatuses[0].ready
```

Expected: a single Pod, `READY=true`, age < 5 minutes (it just restarted because the ConfigMap changed).

- [ ] **Step 4.6: Run smoke**

```bash
bash tests/2026-05-05-phase6-fifo-smoke.sh
```

Expected output (last few lines):

```
[step 5/5] waiting up to 120s for d-BIG and d-SMALL pod startTime
OK: d-BIG (...) scheduled before d-SMALL (...) — sla worked

=== PHASE 6 SMOKE PASSED ===
```

Exit code: 0.

If smoke FAILS at step 5 ("d-BIG scheduled AFTER d-SMALL — leapfrog still happens"): the sla plugin is configured but didn't reserve. Check `kubectl -n lolday logs deploy/lolday-scheduler | grep sla` for plugin errors. If smoke fails at steps 1 or 2: re-check Tasks 2 and 3.

- [ ] **Step 4.7: No commit** (this task is verification only — no source changes).

---

## Task 5: 6d — Documentation updates

**Files:**

- Modify: `docs/architecture.md` (find §10 / Phase 1 / Phase 2 references)
- Modify: `.claude/rules/charts-and-helm.md` (add note under volcano-queue.yaml entry)

- [ ] **Step 5.1: Find the right paragraphs in architecture.md**

```bash
grep -n "requests.nvidia.com/gpu\|Phase 1.*resolved\|2026-05-05" docs/architecture.md | head -20
```

Identify (a) the §10 entry that mentions Phase 1's `requests.nvidia.com/gpu: 2` quota as a defense, and (b) the §10 entry that mentions Phase 2's per-user queue / DRF.

- [ ] **Step 5.2: Edit `docs/architecture.md`**

For the §10 Phase 1 entry mentioning `requests.nvidia.com/gpu: 2` as a defense, replace the GPU-axis sentence with: "The GPU axis was removed in Phase 6 (2026-05-05) — Volcano queue capability is now the sole GPU gatekeeper. See `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §6.1."

For the §10 Phase 2 entry, append: "Phase 6 (2026-05-05) added the `sla` plugin in tier 1 to prevent the head-of-line leapfrog observed in spec §4.4."

(Exact sentences depend on how §10 is currently written — read the surrounding paragraph and integrate cleanly. Do not introduce new headings or restructure §10.)

- [ ] **Step 5.3: Edit `.claude/rules/charts-and-helm.md`**

Find the line beginning `- volcano-queue.yaml`. Below it, add:

```markdown
- `volcano.custom.scheduler_config_override` in `charts/lolday/values.yaml` is the **only** place the Volcano scheduler config is overridden (Phase 6, 2026-05-05). It replaces the entire sub-chart default. When upgrading the Volcano sub-chart, diff the new sub-chart's `installer/helm/chart/volcano/config/volcano-scheduler.conf` against our override and reconcile manually. Spec: `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §6.2.
```

- [ ] **Step 5.4: Run pre-commit hooks**

```bash
pre-commit run --files docs/architecture.md .claude/rules/charts-and-helm.md
```

Expected: all hooks pass (prettier may reflow the markdown).

- [ ] **Step 5.5: Commit**

```bash
git add docs/architecture.md .claude/rules/charts-and-helm.md
git commit -m "$(cat <<'EOF'
docs(phase6): record GPU FIFO + sla plugin architecture changes

architecture.md §10 — Phase 1 quota no longer defends GPU axis;
Phase 2 entry now references Phase 6 sla plugin addition.
charts-and-helm rule — note that scheduler_config_override is the
only override point and how to reconcile on Volcano sub-chart upgrade.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Bump chart version + finalise (optional — operator decision)

**Files:**

- Modify: `charts/lolday/Chart.yaml` (version + appVersion)

The repo convention is one chart bump per phase release (per `docs/conventions.md` §4). Phase 6 is a small chart change (2 files); the operator can either bundle it into a future combined release or cut a `v0.18.0` immediately.

- [ ] **Step 6.1: Decide bundling strategy**

Ask the operator: "Cut a v0.18.0 release for Phase 6 alone, or hold for a combined release later?" Skip Step 6.2 if holding.

- [ ] **Step 6.2: Bump version (if cutting)**

Edit `charts/lolday/Chart.yaml`: `version: 0.17.0` → `version: 0.18.0`, and `appVersion: "0.17.0"` → `appVersion: "0.18.0"`.

- [ ] **Step 6.3: Commit**

```bash
git add charts/lolday/Chart.yaml
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.18.0 — phase 6 GPU FIFO + anti-starvation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Long-running validation (24h post-deploy, observation only)

No file edits. After Tasks 1–5 are merged + deployed, observe the live cluster for 24 hours:

- [ ] **Step 7.1: Sample 24h after deploy**

```bash
# (a) submission-vs-start order: any inversion = potential issue
kubectl get jobs.batch.volcano.sh -A \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,CREATED:.metadata.creationTimestamp,STATE:.status.state.phase \
  --sort-by=.metadata.creationTimestamp \
  | tail -30

# (b) per-job actual start times
kubectl get pods -A -l volcano.sh/job-name \
  -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,CREATED:.metadata.creationTimestamp,START:.status.startTime \
  --sort-by=.status.startTime \
  | tail -30
```

Manually compare: jobs created earlier should generally start earlier. A few inversions for short small jobs running before older big jobs are OK as long as they're not perpetual.

- [ ] **Step 7.2: Sample Prometheus**

In Grafana, query `lolday_jobs_pending_seconds` (Phase 4 metric). Look at p99 over the last 24h. Threshold: should be < 10 minutes + the 90th-percentile `active_deadline_seconds` for the heaviest profile (currently 6h for train, so worst-case ~6h10m). If p99 > that, sla isn't keeping up — escalate.

- [ ] **Step 7.3: No commit** (observation only).

---

## Summary

7 tasks, 5 producing commits (`smoke`, `quota`, `sla`, `docs`, optional `version-bump`), 2 verification-only (`deploy + smoke`, `24h validation`). Smoke is written first per TDD discipline; the rest of the chart changes are atomic enough to land as separate commits and rollback cleanly via `helm rollback`.
