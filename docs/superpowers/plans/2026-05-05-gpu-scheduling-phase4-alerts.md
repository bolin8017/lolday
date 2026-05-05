# Phase 4: Resource-pressure Alerts + Pending Gauge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Prometheus alerts for the failure surfaces Phase 0–1 protections defend against (host MemoryPressure / DiskPressure, GPU VRAM, ResourceQuota saturation) plus a `lolday_jobs_pending_total` Gauge so the existing alertmanager → Discord pipeline surfaces them. Closes spec §7 Phase 4.

**Architecture:** Extends the existing `lolday-baseline.rules` PrometheusRule under `charts/lolday/templates/monitoring/alertmanager-rules.yaml`. The Gauge is set inside `services/cluster_status.get_queue_depth()` (already runs every 10s via TTLCache; piggyback rather than introducing a second loop). All alert conditions use upstream Prometheus exporters already deployed (kube-state-metrics, NVIDIA DCGM, node-exporter) — no new exporters added.

**Tech Stack:** prometheus_client.Gauge (Python), kube-prometheus-stack, NVIDIA DCGM exporter, kube-state-metrics, AlertManager Discord routing (already wired in Phase 6).

**Spec:** `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §7 Phase 4. Note **revised scope vs spec**: the spec originally proposed a backend `JOB_PER_USER_OPEN_LIMIT` to cap pending; investigation showed `JOB_PER_USER_CONCURRENCY=2` already counts pending (`NON_TERMINAL_STATUSES = {PENDING, PREPARING, RUNNING}`), so a second cap would be redundant. Phase 4 reduces to instrumentation + alerts. Scenario §5.2 in the spec is amended.

**Pre-requisite:** Phase 0 (kubelet reservations, PR #86) + Phase 1 (lolday-jobs ns + ResourceQuota, PR #87 + #88) **already merged + applied on server30**. The Quota usage alert depends on Phase 1's quotas existing.

---

## File map

**New files:**

- `tests/2026-05-05-phase4-alerts-smoke.sh` — verifies the new PrometheusRule + Gauge are applied / scraped.

**Modified files:**

- `backend/app/metrics.py` — add `JOBS_PENDING_TOTAL` Gauge.
- `backend/app/services/cluster_status.py` — set the new Gauge inside `get_queue_depth()`'s side-effect block (next to the existing `VOLCANO_PENDING_STALE.set(...)`).
- `charts/lolday/templates/monitoring/alertmanager-rules.yaml` — append a new group `lolday-resource-pressure.rules` with 6 alerts.
- `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` — amend §5.2 (scenario B no longer applies because of pre-existing PER_USER_CONCURRENCY semantics) and §7 Phase 4 (drop `JOB_PER_USER_OPEN_LIMIT`).
- `docs/architecture.md` §10 — add a gotcha entry pointing at the new alerts.

**Not touched:** `backend/app/routers/jobs.py` (no new cap needed), `backend/app/config.py` (no new env var). Two-line diff savings versus the original spec.

---

## Execution order

```
Wave 0 (parallel — chart + backend instrumentation)
├── Task 1: branch
├── Task 2: metrics.py — add JOBS_PENDING_TOTAL
├── Task 3: cluster_status.py — set the Gauge
└── Task 4: alertmanager-rules.yaml — append the new group

Wave 1 (sequential — verification + docs)
├── Task 5: smoke test
├── Task 6: spec amendment + architecture.md note
└── Task 7: pre-commit + commit + push + PR

Wave 2 (operator-attended)
└── Task 8: deploy.sh + smoke
```

---

## Task 1: Branch

```bash
git checkout main && git pull --rebase
git checkout -b feat/gpu-scheduling-phase4-alerts-queue-depth
```

(Already created at the start of this session.)

---

## Task 2: `backend/app/metrics.py` — add `JOBS_PENDING_TOTAL`

**Files:** Modify `backend/app/metrics.py`

- [ ] Append to the bottom of the file:

```python
# Phase 4 — every 10s update via cluster_status.get_queue_depth(). Distinct
# from VOLCANO_PENDING_STALE (which counts only Pending older than the stale
# threshold); this Gauge is the *total* non-terminal vcjob count, tracked so
# operators can see queue growth before any single job becomes "stale".
JOBS_PENDING_TOTAL = Gauge(
    "lolday_jobs_pending_total",
    "Total non-terminal Volcano Jobs in the lolday-jobs queue (Pending + "
    "Running). Refreshed every 10s by services.cluster_status.get_queue_depth.",
)
```

---

## Task 3: `backend/app/services/cluster_status.py` — set the Gauge

**Files:** Modify `backend/app/services/cluster_status.py`

- [ ] Step 1: extend the import at line 23

Find:

```python
from app.metrics import BACKEND_ERRORS, VOLCANO_PENDING_STALE
```

Replace:

```python
from app.metrics import BACKEND_ERRORS, JOBS_PENDING_TOTAL, VOLCANO_PENDING_STALE
```

- [ ] Step 2: set the new gauge in `get_queue_depth`

Find the side-effect block ending with `return len(non_terminal)`:

```python
        VOLCANO_PENDING_STALE.set(stale)
    except Exception:
        BACKEND_ERRORS.labels(stage="queue_stale_gauge").inc()
        logger.exception("stale-gauge refresh failed (queue=%s)", queue_name)

    return len(non_terminal)
```

Replace with:

```python
        VOLCANO_PENDING_STALE.set(stale)
    except Exception:
        BACKEND_ERRORS.labels(stage="queue_stale_gauge").inc()
        logger.exception("stale-gauge refresh failed (queue=%s)", queue_name)

    # Phase 4 — total non-terminal vcjob count for queue-depth alerting.
    # Distinct from VOLCANO_PENDING_STALE (counts only Pending older than the
    # stale threshold). Setting outside the try/except above is intentional:
    # this number is `len(non_terminal)` which is already known and cannot
    # raise.
    JOBS_PENDING_TOTAL.set(len(non_terminal))

    return len(non_terminal)
```

---

## Task 4: `alertmanager-rules.yaml` — append `lolday-resource-pressure.rules` group

**Files:** Modify `charts/lolday/templates/monitoring/alertmanager-rules.yaml`

> Appended as a new group, NOT inside the existing `lolday-baseline.rules` group. Keeps blast-radius / expression-style boundaries clean (baseline = always-on, resource-pressure = the new safety net).

- [ ] Append, after the `- name: lolday-trivy.rules` group and before the closing `{{- end }}`:

```yaml
- name: lolday-resource-pressure.rules
  interval: 30s
  rules:
    # Phase 4 — kubelet eviction signal. Phase 0 added eviction-soft
    # memory.available<2Gi (grace 2m); when that fires, kubelet
    # marks the Node MemoryPressure=true. This alert reflects that
    # *as it is happening* — at this point pods are getting evicted.
    - alert: LoldayNodeMemoryPressure
      expr: kube_node_status_condition{condition="MemoryPressure",status="true"} == 1
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "{{`{{ $labels.node }}`}} entered MemoryPressure — kubelet evicting pods"
        description: "Node {{`{{ $labels.node }}`}} crossed eviction-soft memory.available<2Gi for >1m. kubelet is evicting BestEffort/Burstable pods. Investigate biggest offenders: `kubectl top pods -A --sort-by=memory`. See spec §5.1."

    - alert: LoldayNodeDiskPressure
      expr: kube_node_status_condition{condition="DiskPressure",status="true"} == 1
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "{{`{{ $labels.node }}`}} entered DiskPressure — kubelet evicting pods"
        description: "Node {{`{{ $labels.node }}`}} fell below eviction-hard nodefs/imagefs<10%. kubelet is evicting pods. Free disk: prune Harbor old tags, prune old Prometheus TSDB blocks, or `docker system prune` on root LV. See spec §5.5."

    # Phase 4 — VRAM early warning. NVIDIA does not expose a K8s-native
    # GPU memory limit primitive (no MIG on 2080 Ti, no HAMi by hard rule),
    # so VRAM exhaustion surfaces only as a CUDA OOM mid-training.
    # This alert lets us see the high-water before the next batch fails.
    - alert: LoldayGPUVRAMHigh
      expr: |
        max by (gpu, Hostname) (
          DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE)
        ) > 0.9
      for: 3m
      labels:
        severity: warning
      annotations:
        summary: "GPU {{`{{ $labels.gpu }}`}} VRAM > 90% for 3m"
        description: "GPU {{`{{ $labels.gpu }}`}} on {{`{{ $labels.Hostname }}`}} has used >90% of its physical VRAM (currently {{`{{ $value | humanizePercentage }}`}}) for the last 3m. Next allocation by the running detector will trigger CUDA OOM. Inspect: `kubectl -n lolday-jobs logs <vcjob-pod> -c detector`. Mitigation: lower batch_size in maldet config or use the smaller `gpu1` resource_profile (Phase 3)."

    # Phase 4 — quota saturation. The `lolday-jobs-quota` from Phase 1
    # caps total cluster usage of the workload ns; once it's near full,
    # new submissions get HTTP 429 (or vcjob admission rejection).
    - alert: LoldayJobsQuotaMemoryNearLimit
      expr: |
        sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.memory",type="used"})
        /
        sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.memory",type="hard"})
        > 0.85
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "lolday-jobs ResourceQuota requests.memory > 85%"
        description: "lolday-jobs has used {{`{{ $value | humanizePercentage }}`}} of its requests.memory quota for >5m. New submissions risk admission rejection. Investigate: `kubectl describe quota -n lolday-jobs`. See spec §7 Phase 1."

    - alert: LoldayJobsQuotaCPUNearLimit
      expr: |
        sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.cpu",type="used"})
        /
        sum(kube_resourcequota{namespace="lolday-jobs",resource="requests.cpu",type="hard"})
        > 0.85
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "lolday-jobs ResourceQuota requests.cpu > 85%"
        description: "lolday-jobs has used {{`{{ $value | humanizePercentage }}`}} of its requests.cpu quota for >5m. Investigate: `kubectl describe quota -n lolday-jobs`."

    # Phase 4 — workload backlog. Distinct from VolcanoJobsStuckPending
    # (which fires when a single job is Pending > 30min); this one fires
    # when the *backlog* is large enough to be a UX problem regardless
    # of any one job's age.
    - alert: LoldayPendingJobsHigh
      expr: lolday_jobs_pending_total > 12
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "{{`{{ $value }}`}} non-terminal vcjobs in queue (>12 for 10m)"
        description: "More than 12 vcjobs are pending or running for >10m. Quota count/jobs.batch.volcano.sh hard cap is 20; we're approaching saturation. Likely cause: GPU-heavy workload contention. Spec §6.3 covers per-user fair-share (Phase 2)."
```

---

## Task 5: Smoke test

**Files:** Create `tests/2026-05-05-phase4-alerts-smoke.sh`

- [ ] Author:

```bash
#!/usr/bin/env bash
# Smoke: Phase 4 — resource-pressure alerts deployed.
set -euo pipefail

NS_MON=${NS_MON:-monitoring}
NS_INFRA=${NS_INFRA:-lolday}
fail=0

echo "[step 1/4] PrometheusRule lolday-baseline contains the new group"
groups=$(kubectl -n "${NS_MON}" get prometheusrule lolday-baseline \
  -o jsonpath='{.spec.groups[*].name}' 2>/dev/null)
if echo "${groups}" | grep -q "lolday-resource-pressure.rules"; then
  echo "OK: groups = ${groups}"
else
  echo "FAIL: lolday-resource-pressure.rules missing; groups=${groups}"
  fail=1
fi

echo ""
echo "[step 2/4] all 6 new alerts present"
expected="LoldayNodeMemoryPressure LoldayNodeDiskPressure LoldayGPUVRAMHigh LoldayJobsQuotaMemoryNearLimit LoldayJobsQuotaCPUNearLimit LoldayPendingJobsHigh"
alerts=$(kubectl -n "${NS_MON}" get prometheusrule lolday-baseline \
  -o jsonpath='{.spec.groups[*].rules[*].alert}' 2>/dev/null)
miss=0
for a in $expected; do
  if ! echo "${alerts}" | grep -q "${a}"; then
    echo "FAIL: missing ${a}"
    miss=$((miss+1))
  fi
done
if [ "${miss}" -eq 0 ]; then
  echo "OK: all 6 alerts present"
else
  echo "FAIL: ${miss} alert(s) missing"
  fail=1
fi

echo ""
echo "[step 3/4] backend exposes lolday_jobs_pending_total"
out=$(kubectl -n "${NS_INFRA}" exec deploy/backend -c backend -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())" 2>/dev/null \
  | grep -E "^lolday_jobs_pending_total" || true)
if [ -n "${out}" ]; then
  echo "OK: ${out}"
else
  echo "FAIL: lolday_jobs_pending_total not in /metrics"
  fail=1
fi

echo ""
echo "[step 4/4] Prometheus is scraping the alert (rule is loaded)"
# Cheap proxy: check Prometheus's own /api/v1/rules endpoint via port-forward
# would be the proper way. Skip if Prometheus pod isn't named predictably.
prom_pod=$(kubectl -n "${NS_MON}" get pods -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "${prom_pod}" ]; then
  loaded=$(kubectl -n "${NS_MON}" exec "${prom_pod}" -c prometheus -- \
    wget -q -O - http://localhost:9090/api/v1/rules 2>/dev/null \
    | grep -c "LoldayPendingJobsHigh" || true)
  if [ "${loaded}" -gt 0 ]; then
    echo "OK: Prometheus loaded LoldayPendingJobsHigh rule"
  else
    echo "WARN: Prometheus has not yet loaded the rule (may need 30-60s after deploy)"
  fi
else
  echo "WARN: could not find prometheus pod by label; skipping rules check"
fi

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
```

`chmod +x tests/2026-05-05-phase4-alerts-smoke.sh && bash -n tests/2026-05-05-phase4-alerts-smoke.sh`

---

## Task 6: spec amendment + architecture note

- [ ] Step 1: spec — strike out §5.2 with the corrected analysis.

In `docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md` §5.2 prepend a note above the original block:

```markdown
> **Amendment 2026-05-05 (during Phase 4 implementation):** the original premise of this scenario was wrong. `JOB_PER_USER_CONCURRENCY=2` already counts pending — `routers/jobs.py:262` filters by `NON_TERMINAL_STATUSES = {PENDING, PREPARING, RUNNING}`, all three statuses. A single user therefore cannot accumulate >2 open jobs regardless of POST rate. The runaway path described below is closed by the existing cap. Phase 4 drops `JOB_PER_USER_OPEN_LIMIT` and reduces to instrumentation + alerts.
```

- [ ] Step 2: spec — `### Phase 4` heading in §7, replace the body's "新增 metric" + "在 routers/jobs.py:262 之上補一條" sub-blocks with a single line:

```markdown
**Implementation:** new `JOBS_PENDING_TOTAL` Gauge in `metrics.py` set inside `services/cluster_status.get_queue_depth()` (already runs every 10s). New `lolday-resource-pressure.rules` group in `monitoring/alertmanager-rules.yaml` with the 6 alerts above. **No backend cap added** — see §5.2 amendment.
```

- [ ] Step 3: architecture.md §10 — append a new common gotcha:

```markdown
14. **Phase 4 alerts route to Discord** — `LoldayNodeMemoryPressure` / `LoldayNodeDiskPressure` (critical) → critical webhook, `LoldayGPUVRAMHigh` / `LoldayJobsQuotaMemoryNearLimit` / `LoldayJobsQuotaCPUNearLimit` / `LoldayPendingJobsHigh` (warning) → warning webhook. Routing matrix in `templates/monitoring/alertmanager-config-discord.yaml`.
```

(Or correct the number — currently §10 has 13 entries; this becomes 14.)

---

## Task 7: pre-commit + commit + push + PR

- [ ] `git add` the 6 changed/new files
- [ ] `pre-commit run --files <list>`
- [ ] `git commit -m "feat(monitoring): phase 4 — resource-pressure alerts + jobs-pending gauge"` (full body in conventional-commit format pointing at spec/plan)
- [ ] `git push -u origin feat/gpu-scheduling-phase4-alerts-queue-depth`
- [ ] `gh pr create --title "feat(monitoring): phase 4 — resource-pressure alerts + jobs-pending gauge" --body ...`

---

## Task 8: deploy + smoke (operator-attended)

- [ ] `bash scripts/deploy.sh`
- [ ] `bash tests/2026-05-05-phase4-alerts-smoke.sh`
- [ ] Optionally trigger `LoldayGPUVRAMHigh` by submitting a memory-bloating evaluate (`stress-ng --vm 1 --vm-bytes 10G --timeout 5m` running on GPU); verify Discord receives it.

---

## Self-review checklist

- [ ] All 6 alert expressions resolve to real metrics (kube-state-metrics + DCGM + node-exporter + the new gauge — all already deployed).
- [ ] `for:` durations match the spec table.
- [ ] Severity labels match existing pattern (critical / warning).
- [ ] Annotation `summary` is short (one line); `description` is operator-actionable (next command, link to spec section).
- [ ] No new env var added (recap: scope reduction vs spec).
- [ ] `JOBS_PENDING_TOTAL.set(...)` runs unconditionally; `len(non_terminal)` cannot raise.
- [ ] Smoke test validates the rule is loaded by Prometheus (not just applied to K8s).
