# Alerting Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace noisy GPU temp/VRAM telemetry alerts with NVIDIA-recommended fault detection, split Alertmanager routing into a clean critical-only Captain Hook + new Spidey Warnings channel, add 5 inhibition rules to suppress cascade, and expose a `lolday_gpu_signal_fail_safe_active` Prometheus metric so admins can see when the host-aware GPU scheduler (議題 A) drops to fail-safe.

**Architecture:** Two chart yaml files own all alerting config (`alertmanager-rules.yaml` for PrometheusRule, `alertmanager-config-discord.yaml` for AlertmanagerConfig CR — routing + inhibit + receivers). One backend module gets a 1-line metric export (`gpu_signal.py`) plus a Gauge declaration in `metrics.py`. The 4 Discord channels are wired through 2 Secret keys (`webhook-url-critical` / `webhook-url-warning`); `scripts/deploy.sh` already requires both env vars, so the only operator-side change is pointing `DISCORD_WEBHOOK_URL_WARNING` at the newly-created Spidey Warnings channel before deploy.

**Tech Stack:** Helm + AlertmanagerConfig CRD + PrometheusRule CRD + DCGM exporter (gpu-operator default), Python 3.12 + prometheus-client (backend), promtool + amtool + yq + helm template (tests).

**Reference:** Spec — `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`.

**Branch setup (do this BEFORE Task 2):**

```bash
git checkout main && git pull origin main
git checkout -b feat/alerting-redesign
```

All code commits in Tasks 2-9 land on this branch. Task 10 pushes and opens the PR.

---

## File Structure

### Backend — to modify

| Path                                        | Change                                                               |
| ------------------------------------------- | -------------------------------------------------------------------- |
| `backend/app/metrics.py`                    | Add `GPU_SIGNAL_FAIL_SAFE_ACTIVE` Gauge declaration.                 |
| `backend/app/services/gpu_signal.py`        | Set the Gauge to 1/0 in both branches of `compute_real_gpu_state()`. |
| `backend/tests/services/test_gpu_signal.py` | Add 3 unit tests (set on fail-safe / set on success / transitions).  |

### Charts — to modify

| Path                                                                  | Change                                                                                                                                                                                                                                                                             |
| --------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `charts/lolday/templates/monitoring/alertmanager-rules.yaml`          | Remove 2 rules (GPUTemperatureHigh, LoldayGPUVRAMHigh), modify 2 (`PodCrashLoopBackOff for: 5m→15m`, `TrivyCriticalCVE` severity demote `critical→warning` + reword summary), add 4 (DCGMXIDError, DCGMThrottleReasonsPersistent, GpuSignalFailSafeStuck, GpuSignalCountMismatch). |
| `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml` | Per-receiver `repeatInterval` (`critical: 4h`, `warning: 24h`) + add 5 inhibitRules (defined in spec §6.2). No receiver YAML changes (already split critical/warning since Phase 7.1).                                                                                             |

### Tests — to create

| Path                                               | Change                                                                                                                                                                                     |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/phase7/test_alert_rules_inventory.sh`       | New chart contract test: render chart, assert exactly the 16 expected alerts present, none extra; assert removed rules absent; assert key fields per rule (severity, for, expr substring). |
| `tests/2026-05-10-alerting-redesign-promtool.yaml` | Promtool unit-test fixture: input series + expected firing windows for the 4 new alerts + the 2 modified ones.                                                                             |
| `tests/2026-05-10-alerting-redesign-smoke.sh`      | Live smoke (operator-run, post-deploy): inject test alerts via Alertmanager API, observe Discord.                                                                                          |

### Docs — to modify

| Path                               | Change                                                                                                                |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `docs/architecture.md`             | Add §10 entry "Alerting redesign (2026-05-10)" + reference to spec.                                                   |
| `docs/runbooks/troubleshooting.md` | 3 new SOPs (DCGMXIDError fired / GpuSignalFailSafeStuck fired / cascade noise).                                       |
| `.claude/rules/charts-and-helm.md` | Update the `alertmanager-rules.yaml` + `alertmanager-config-discord.yaml` bullet to describe the new 4-channel split. |

### Memory — to update

| Path                                                                                                    | Change                                                                                 |
| ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/reference_discord_channels.md` | Add the new "Spidey Warnings" channel id (operator pastes after creating the channel). |

---

## Task 1: Operator pre-deploy checklist (no code)

**Files:** none (operator-driven; documented in this plan)

> **This task does NOT produce a commit.** It is the pre-deploy checklist the operator must complete before any of the following code changes ship live. Document it explicitly so it does not become a "forgot to do this" silent failure.

- [ ] **Step 1: Create the Discord channel**

In the lolday Discord server, create a new text channel named `Spidey Warnings` (or whatever your local naming convention prefers — the channel name is not chart-side, the webhook URL is what wires it).

- [ ] **Step 2: Add an incoming webhook integration**

Channel settings → Integrations → Webhooks → New Webhook → Copy the webhook URL.

- [ ] **Step 3: Update local secrets**

Edit `~/.lolday-secrets.env` and replace the existing `DISCORD_WEBHOOK_URL_WARNING` value with the new channel's webhook URL. The variable already exists today (currently pointing at Captain Hook); just swap the URL.

- [ ] **Step 4: Note the channel id for memory**

Right-click the new channel in Discord → Copy Channel ID. Save the id; it goes into memory in Task 8 below.

- [ ] **Step 5: Verify (still no deploy)**

```bash
grep '^DISCORD_WEBHOOK_URL_WARNING=' ~/.lolday-secrets.env
```

Expected: a single line whose URL ends with the new webhook id (a long path segment after `/webhooks/<channel-id>/`).

> Do not run `bash scripts/deploy.sh` yet — wait until all the code tasks below are merged so the chart change and the secret swap land together.

---

## Task 2: Add `GPU_SIGNAL_FAIL_SAFE_ACTIVE` Gauge + integrate (TDD)

**Files:**

- Modify: `backend/app/metrics.py`
- Modify: `backend/app/services/gpu_signal.py`
- Modify: `backend/tests/services/test_gpu_signal.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/services/test_gpu_signal.py`:

```python
def test_metric_set_to_one_on_fail_safe():
    """When Prom is unreachable, fail-safe metric must be 1."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    with patch(
        "app.services.gpu_signal._query_prometheus",
        side_effect=gpu_signal.PrometheusUnavailable("simulated"),
    ), _override_settings(2):
        gpu_signal.compute_real_gpu_state()

    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 1.0


def test_metric_set_to_zero_on_success():
    """When Prom returns cleanly, fail-safe metric must be 0."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    # Pre-set to 1 to verify it actually transitions
    GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(1)

    with _patch_queries([], [], []), _override_settings(2):
        gpu_signal.compute_real_gpu_state()

    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 0.0


def test_metric_value_updates_when_state_transitions():
    """Sequential calls must reflect each call's outcome."""
    from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE

    # Round 1: success -> 0
    with _patch_queries([], [], []), _override_settings(2):
        gpu_signal.compute_real_gpu_state()
    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 0.0

    # Round 2: fail-safe -> 1
    with patch(
        "app.services.gpu_signal._query_prometheus",
        side_effect=gpu_signal.PrometheusUnavailable("simulated"),
    ), _override_settings(2):
        gpu_signal.compute_real_gpu_state()
    assert GPU_SIGNAL_FAIL_SAFE_ACTIVE._value.get() == 1.0
```

- [ ] **Step 2: Verify failure**

```bash
cd backend && uv run pytest tests/services/test_gpu_signal.py -v -k metric
```

Expected: 3 tests fail with `ImportError` on `GPU_SIGNAL_FAIL_SAFE_ACTIVE` (not yet defined).

- [ ] **Step 3: Add the Gauge declaration**

Append to `backend/app/metrics.py` (after the existing Gauges, keep alphabetical or arrival order — match the file's convention):

```python
# 議題 B (alerting redesign) — exposes gpu_signal's fail-safe state as a
# Gauge so Alertmanager can fire `GpuSignalFailSafeStuck` when Prometheus
# is unreachable for >30 min.  See
# docs/superpowers/specs/2026-05-10-alerting-redesign-design.md §6.5.
GPU_SIGNAL_FAIL_SAFE_ACTIVE = Gauge(
    "lolday_gpu_signal_fail_safe_active",
    "1 when gpu_signal cannot reach Prom (fail-safe path active), else 0.",
)
```

- [ ] **Step 4: Set the Gauge in `gpu_signal.py`**

In `backend/app/services/gpu_signal.py`, edit `compute_real_gpu_state()` so both return paths update the metric.

Add to imports:

```python
from app.metrics import GPU_SIGNAL_FAIL_SAFE_ACTIVE
```

In the `except PrometheusUnavailable as e:` branch, immediately before the `return GPUState(...)`:

```python
        GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(1)
```

After `_classify_gpus(...)` and before the success-path `return GPUState(...)`:

```python
    GPU_SIGNAL_FAIL_SAFE_ACTIVE.set(0)
```

- [ ] **Step 5: Verify pass**

```bash
cd backend && uv run pytest tests/services/test_gpu_signal.py -v
```

Expected: 19 PASSED (16 prior + 3 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/metrics.py backend/app/services/gpu_signal.py backend/tests/services/test_gpu_signal.py
git commit -m "feat(metrics): expose lolday_gpu_signal_fail_safe_active gauge

Backend gpu_signal now sets the Gauge to 1 when Prom is unreachable
(fail-safe path) and 0 on success. Powers the GpuSignalFailSafeStuck
alert added in subsequent tasks."
```

---

## Task 3: Update PrometheusRule alert inventory (chart yaml)

**Files:**

- Modify: `charts/lolday/templates/monitoring/alertmanager-rules.yaml`

- [ ] **Step 1: Remove `GPUTemperatureHigh` and `LoldayGPUVRAMHigh`**

Open `charts/lolday/templates/monitoring/alertmanager-rules.yaml`. Delete two stanzas:

1. The whole `- alert: GPUTemperatureHigh` block (currently lines ~28–35; remove until the next `- alert:` or empty line).
2. The whole `- alert: LoldayGPUVRAMHigh` block (currently lines ~118–128).

- [ ] **Step 2: Modify `PodCrashLoopBackOff for: 5m → 15m`**

Find the `- alert: PodCrashLoopBackOff` block. Change `for: 5m` to `for: 15m`. Update the description annotation: replace "for 5m" with "for 15m" so the body text matches the new threshold.

- [ ] **Step 3: Demote `TrivyCriticalCVE` severity**

Find `- alert: TrivyCriticalCVE`. Change `labels.severity: critical` to `labels.severity: warning`. Update annotations:

- `summary`: keep as-is (still relevant)
- `description`: prepend a sentence noting "Severity demoted from critical to warning per spec 2026-05-10-alerting-redesign-design (§5.6)."

- [ ] **Step 4: Add `DCGMXIDError`**

Add this new alert to the `lolday-baseline.rules` group (alongside the other DCGM-derived rules — at the spot where `GPUTemperatureHigh` was removed, so the GPU-related alerts cluster together):

```yaml
- alert: DCGMXIDError
  expr: DCGM_FI_DEV_XID_ERRORS > 0
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "GPU {{`{{ $labels.gpu }}`}} on {{`{{ $labels.Hostname }}`}} reported NVIDIA XID error"
    description: "DCGM reported a non-zero XID error code on GPU {{`{{ $labels.gpu }}`}} ({{`{{ $labels.Hostname }}`}}). XID codes indicate driver-level faults (memory error, MMU fault, falling off the bus, etc.). See https://docs.nvidia.com/deploy/xid-errors/ to interpret the code; some are transient (kernel preempt) but most indicate hardware degradation. Investigate `dcgmi diag` output before lolday job submissions resume."
```

- [ ] **Step 5: Add `DCGMThrottleReasonsPersistent`**

Add to `lolday-resource-pressure.rules` group (the warning-grade GPU/quota cluster):

```yaml
- alert: DCGMThrottleReasonsPersistent
  expr: DCGM_FI_DEV_CLOCKS_THROTTLE_REASON_HW_THERMAL_SLOWDOWN > 0
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "GPU {{`{{ $labels.gpu }}`}} on {{`{{ $labels.Hostname }}`}} sustained HW thermal throttle for 10m"
    description: "DCGM reports the GPU has been in hardware thermal slowdown for 10 minutes — sustained throttle indicates either an inadequate cooling solution or a thermal event in progress. Distinct from a brief temperature spike during normal training (which we deliberately do NOT alert on; spec §4.1). Investigate fan / chassis airflow / ambient temperature."
```

- [ ] **Step 6: Add `GpuSignalFailSafeStuck`**

Add to `lolday-resource-pressure.rules` group:

```yaml
- alert: GpuSignalFailSafeStuck
  expr: lolday_gpu_signal_fail_safe_active == 1
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "lolday gpu_signal in fail-safe for 30m — scheduler not dispatching"
    description: "The host-aware GPU signal (議題 A) has been in fail-safe (Prometheus unreachable from backend) for 30 minutes. The FIFO scheduler is fail-closed: new jobs queued but not dispatched. Check `kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheus` and the backend → kps-prometheus.monitoring.svc network path. Spec: docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md §7."
```

- [ ] **Step 7: Add `GpuSignalCountMismatch`**

Add to `lolday-resource-pressure.rules` group:

```yaml
- alert: GpuSignalCountMismatch
  expr: increase(lolday_backend_errors_total{stage="gpu_signal_count_mismatch"}[10m]) > 0
  for: 0m
  labels:
    severity: warning
  annotations:
    summary: "DCGM reports gpu_id outside CLUSTER_PHYSICAL_GPU_COUNT — env var stale?"
    description: 'Backend gpu_signal saw a DCGM sample with a gpu label beyond `CLUSTER_PHYSICAL_GPU_COUNT` ({{`{{ with query "max(lolday_gpu_signal_fail_safe_active)" }}{{ . | first | value }}{{ end }}`}}). Hardware was likely upgraded without bumping the env var; lolday is silently dropping the extra GPU(s). Action: bump `CLUSTER_PHYSICAL_GPU_COUNT` in chart values + redeploy.'
```

- [ ] **Step 8: Render the chart and confirm yaml is well-formed**

```bash
helm template lolday charts/lolday \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > /tmp/render.yaml
```

Expected: succeeds, no template errors. Then:

```bash
yq eval-all '
  select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline")
  | .spec.groups[].rules[].alert
' /tmp/render.yaml | sort
```

Expected output (sorted alphabetically — 16 alerts, two old ones absent):

```
AlloyLokiWriteDroppedSamples
DCGMThrottleReasonsPersistent
DCGMXIDError
GpuSignalCountMismatch
GpuSignalFailSafeStuck
LoldayBackendErrorRateElevated
LoldayCoreServiceDown
LoldayJobsQuotaCPUNearLimit
LoldayJobsQuotaMemoryNearLimit
LoldayNodeDiskPressure
LoldayNodeMemoryPressure
LoldayPendingJobsHigh
NodeDiskAlmostFull
PodCrashLoopBackOff
TrivyCriticalCVE
VolcanoJobsStuckPending
```

If any rule is missing or extra, fix the yaml.

- [ ] **Step 9: Commit**

```bash
git add charts/lolday/templates/monitoring/alertmanager-rules.yaml
git commit -m "feat(charts): rewrite alert rule inventory for SRE-aligned signals

- Remove GPUTemperatureHigh + LoldayGPUVRAMHigh (telemetry-derived
  heuristics that fire during normal ML training).
- Replace with NVIDIA-recommended fault detection: DCGMXIDError
  (driver-level fault, critical) + DCGMThrottleReasonsPersistent
  (sustained thermal throttle, warning).
- Add GpuSignalFailSafeStuck + GpuSignalCountMismatch to surface
  silent degradation modes from the host-aware GPU signal (議題 A).
- PodCrashLoopBackOff for: 5m → 15m (kube-prometheus-stack default;
  reduces deploy-time false positives).
- TrivyCriticalCVE severity demoted critical → warning (CVEs are
  actionable but not page-time-sensitive)."
```

---

## Task 4: Update AlertmanagerConfig — routing + inhibition

**Files:**

- Modify: `charts/lolday/templates/monitoring/alertmanager-config-discord.yaml`

- [ ] **Step 1: Add per-receiver `repeatInterval` overrides + inhibition rules**

Replace the current `spec:` body (route + receivers) with:

```yaml
spec:
  route:
    receiver: discord-warning
    groupBy: [alertname, severity]
    groupWait: 30s
    groupInterval: 5m
    repeatInterval: 24h
    routes:
      - receiver: discord-critical
        matchers:
          - name: severity
            value: critical
            matchType: "="
        repeatInterval: 4h
      - receiver: discord-warning
        matchers:
          - name: severity
            value: warning
            matchType: "="
        repeatInterval: 24h
  inhibitRules:
    - sourceMatch:
        - { name: alertname, value: LoldayCoreServiceDown, matchType: "=" }
        - { name: job, value: backend, matchType: "=" }
      targetMatch:
        - {
            name: alertname,
            value: LoldayBackendErrorRateElevated,
            matchType: "=",
          }
      equal: []
    - sourceMatch:
        - { name: alertname, value: LoldayCoreServiceDown, matchType: "=" }
      targetMatch:
        - { name: alertname, value: VolcanoJobsStuckPending, matchType: "=" }
      equal: []
    - sourceMatch:
        - { name: alertname, value: LoldayNodeMemoryPressure, matchType: "=" }
      targetMatch:
        - { name: alertname, value: PodCrashLoopBackOff, matchType: "=" }
      equal: []
    - sourceMatch:
        - { name: alertname, value: LoldayNodeDiskPressure, matchType: "=" }
      targetMatch:
        - { name: alertname, value: PodCrashLoopBackOff, matchType: "=" }
      equal: []
    - sourceMatch:
        - { name: alertname, value: DCGMXIDError, matchType: "=" }
      targetMatch:
        - {
            name: alertname,
            value: DCGMThrottleReasonsPersistent,
            matchType: "=",
          }
      equal: []
  receivers:
    - name: discord-critical
      discordConfigs:
        - apiURL:
            name: alertmanager-discord
            key: webhook-url-critical
          sendResolved: true
          title: "🚨 [CRITICAL] {{`{{ .GroupLabels.alertname }}`}}"
          message: '{{`{{ template "discord.default.message" . }}`}}'
          # @here triggers a Discord channel mention push (requires Alertmanager v0.28+).
          content: "@here"
    - name: discord-warning
      discordConfigs:
        - apiURL:
            name: alertmanager-discord
            key: webhook-url-warning
          sendResolved: true
          title: "⚠️ [WARNING] {{`{{ .GroupLabels.alertname }}`}}"
          message: '{{`{{ template "discord.default.message" . }}`}}'
```

The receivers section is functionally unchanged from today (already split critical/warning since Phase 7.1) — only the routing + inhibitRules sections change.

- [ ] **Step 2: Render and verify the AlertmanagerConfig is well-formed**

```bash
helm template lolday charts/lolday \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > /tmp/render.yaml

yq eval-all '
  select(.kind == "AlertmanagerConfig" and .metadata.name == "discord-receivers")
' /tmp/render.yaml > /tmp/amc.yaml
```

Then:

```bash
# Confirm 5 inhibition rules and 2 receivers
yq eval '.spec.inhibitRules | length' /tmp/amc.yaml
yq eval '.spec.receivers | length' /tmp/amc.yaml
```

Expected: `5` and `2`.

```bash
# Confirm routing has per-severity repeatIntervals
yq eval '.spec.route.routes[].repeatInterval' /tmp/amc.yaml
```

Expected output:

```
4h
24h
```

If any value is wrong, fix the yaml.

- [ ] **Step 3: Validate with amtool (if available locally)**

Optional but recommended — `amtool` ships with prometheus toolchain:

```bash
yq eval '.spec' /tmp/amc.yaml | amtool config check --config.file=/dev/stdin
```

Expected: `Validation: OK`. If amtool not installed locally, skip — CI doesn't gate on this and the helm template render is sufficient.

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/templates/monitoring/alertmanager-config-discord.yaml
git commit -m "feat(charts): split critical/warning routing + 5 inhibit rules

- Per-route repeatInterval: critical 4h (unchanged) / warning 24h (was
  4h; reduces FYI-grade noise).
- 5 inhibitRules suppress predictable cascade: backend-down implies
  error-rate-elevated and volcano-pending-stale; node memory/disk
  pressure implies pod CrashLoop; XID hardware fault implies thermal
  throttle.
- Receivers unchanged: webhook-url-critical → Captain Hook (@here),
  webhook-url-warning → Spidey Warnings (no @here). Operator must point
  DISCORD_WEBHOOK_URL_WARNING at the new channel before next deploy."
```

---

## Task 5: Promtool unit tests for new + modified alerts

**Files:**

- Create: `tests/2026-05-10-alerting-redesign-promtool.yaml`

- [ ] **Step 1: Write the test fixture**

Create `tests/2026-05-10-alerting-redesign-promtool.yaml`:

```yaml
# Promtool unit tests for 議題 B (alerting redesign).
# Run: promtool test rules tests/2026-05-10-alerting-redesign-promtool.yaml
#
# Each test loads the rendered PrometheusRule and asserts firing/non-firing
# at specific time slices. The rule_files entry uses helm template output;
# the operator generates it before running:
#
#   helm template lolday charts/lolday <set ...> > /tmp/render.yaml
#   yq eval-all 'select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline")' \
#     /tmp/render.yaml | yq eval '.spec' - > /tmp/rules.yaml
#   promtool test rules tests/2026-05-10-alerting-redesign-promtool.yaml
#
# The rule_files in this fixture references that /tmp/rules.yaml.

rule_files:
  - /tmp/rules.yaml

tests:
  # ---------------------------------------------------------------------------
  # DCGMXIDError — fires after 1m of XID > 0
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: 'DCGM_FI_DEV_XID_ERRORS{gpu="0", Hostname="server30"}'
        values: "0 0 5 5 5 5"
    alert_rule_test:
      - eval_time: 2m
        alertname: DCGMXIDError
        exp_alerts: []
      - eval_time: 4m
        alertname: DCGMXIDError
        exp_alerts:
          - exp_labels:
              severity: critical
              gpu: "0"
              Hostname: server30

  # ---------------------------------------------------------------------------
  # DCGMThrottleReasonsPersistent — fires only after 10m sustained
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: 'DCGM_FI_DEV_CLOCKS_THROTTLE_REASON_HW_THERMAL_SLOWDOWN{gpu="0"}'
        values: "1+0x12" # 13 minutes of "1"
    alert_rule_test:
      - eval_time: 5m
        alertname: DCGMThrottleReasonsPersistent
        exp_alerts: [] # below 10m hysteresis
      - eval_time: 11m
        alertname: DCGMThrottleReasonsPersistent
        exp_alerts:
          - exp_labels:
              severity: warning
              gpu: "0"

  # ---------------------------------------------------------------------------
  # GpuSignalFailSafeStuck — fires after 30m fail-safe; clears if it drops
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: "lolday_gpu_signal_fail_safe_active"
        values: "1+0x35" # 36 minutes of "1"
    alert_rule_test:
      - eval_time: 25m
        alertname: GpuSignalFailSafeStuck
        exp_alerts: []
      - eval_time: 31m
        alertname: GpuSignalFailSafeStuck
        exp_alerts:
          - exp_labels:
              severity: warning

  - interval: 1m
    input_series:
      # Goes 1 for 20m, then back to 0 — must NOT fire (toggled below 30m)
      - series: "lolday_gpu_signal_fail_safe_active"
        values: "1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0"
    alert_rule_test:
      - eval_time: 25m
        alertname: GpuSignalFailSafeStuck
        exp_alerts: []
      - eval_time: 31m
        alertname: GpuSignalFailSafeStuck
        exp_alerts: [] # transitioned back to 0 at 20m, never spent 30m at 1

  # ---------------------------------------------------------------------------
  # GpuSignalCountMismatch — fires when increase>0 in last 10m
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: 'lolday_backend_errors_total{stage="gpu_signal_count_mismatch"}'
        values: "0 0 0 0 1 1 1 1 1 1 1 1 1 1"
    alert_rule_test:
      - eval_time: 3m
        alertname: GpuSignalCountMismatch
        exp_alerts: []
      - eval_time: 5m
        alertname: GpuSignalCountMismatch
        exp_alerts:
          - exp_labels:
              severity: warning

  # ---------------------------------------------------------------------------
  # PodCrashLoopBackOff — must NOT fire at 10m, must fire at 16m (15m hysteresis)
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: 'kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff", namespace="lolday", pod="backend-abc", container="backend"}'
        values: "1+0x18"
    alert_rule_test:
      - eval_time: 10m
        alertname: PodCrashLoopBackOff
        exp_alerts: []
      - eval_time: 16m
        alertname: PodCrashLoopBackOff
        exp_alerts:
          - exp_labels:
              severity: warning
              namespace: lolday
              pod: backend-abc
              container: backend
              reason: CrashLoopBackOff

  # ---------------------------------------------------------------------------
  # TrivyCriticalCVE — must label as warning now (severity demoted)
  # ---------------------------------------------------------------------------
  - interval: 1m
    input_series:
      - series: 'trivy_image_vulnerabilities{severity="Critical", image_repository="harbor/lolday/sample"}'
        values: "1+0x12"
    alert_rule_test:
      - eval_time: 11m
        alertname: TrivyCriticalCVE
        exp_alerts:
          - exp_labels:
              severity: warning # was 'critical' pre-2026-05-10
```

- [ ] **Step 2: Generate rendered rules and run promtool**

If `promtool` is on PATH:

```bash
helm template lolday charts/lolday \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > /tmp/render.yaml

yq eval-all 'select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline") | .spec' \
  /tmp/render.yaml > /tmp/rules.yaml

promtool test rules tests/2026-05-10-alerting-redesign-promtool.yaml
```

Expected: all tests pass — output ends with `Unit Testing:  ... SUCCESS`.

If promtool is NOT installed locally (it ships with prometheus binary), document the exact install command in the Step 3 commit message and skip — CI will catch this.

- [ ] **Step 3: Commit**

```bash
git add tests/2026-05-10-alerting-redesign-promtool.yaml
git commit -m "test(promtool): unit tests for redesigned alert rules

Covers the 4 new alerts (DCGMXIDError, DCGMThrottleReasonsPersistent,
GpuSignalFailSafeStuck, GpuSignalCountMismatch) and the 2 modified
ones (PodCrashLoopBackOff for: 15m, TrivyCriticalCVE severity warning).
Run: promtool test rules tests/2026-05-10-alerting-redesign-promtool.yaml
(after rendering rules to /tmp/rules.yaml — see fixture preamble)."
```

---

## Task 6: Chart contract test for the alert inventory

**Files:**

- Create: `tests/phase7/test_alert_rules_inventory.sh`

- [ ] **Step 1: Write the contract test**

Create `tests/phase7/test_alert_rules_inventory.sh`. Model it on `tests/phase7/test_trivy_alert_rule.sh` (already in repo) — same `helm template` + `yq` pattern.

```bash
#!/usr/bin/env bash
# 議題 B (2026-05-10 alerting redesign) — chart contract test for the
# PrometheusRule inventory.  Asserts:
#   1. exactly 16 alerts present in the lolday-baseline PrometheusRule
#   2. the 2 removed rules are absent (GPUTemperatureHigh, LoldayGPUVRAMHigh)
#   3. all 4 new rules are present with the right severity
#   4. PodCrashLoopBackOff has for: 15m and severity: warning
#   5. TrivyCriticalCVE severity is now warning (was critical)
#   6. AlertmanagerConfig has 5 inhibitRules + 2 receivers + per-route repeatInterval
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHART="$REPO_ROOT/charts/lolday"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "✗ FAIL: $*" >&2; exit 1; }
pass() { echo "✓ $*"; }

for bin in helm yq; do
  command -v "$bin" >/dev/null || fail "required tool not on PATH: $bin"
done

helm template lolday "$CHART" \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > "$TMPDIR/rendered.yaml" 2> "$TMPDIR/render.err" \
  || { cat "$TMPDIR/render.err" >&2; fail "helm template failed"; }
pass "chart renders"

# --- PrometheusRule inventory ---

yq eval-all '
  select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/pr.yaml"

ALERT_NAMES="$(yq eval '.spec.groups[].rules[].alert' "$TMPDIR/pr.yaml" | sort -u)"
ALERT_COUNT="$(echo "$ALERT_NAMES" | wc -l | tr -d ' ')"

[ "$ALERT_COUNT" = "16" ] \
  || fail "expected 16 alerts in lolday-baseline; got $ALERT_COUNT.  Names:
$ALERT_NAMES"
pass "16 alerts present"

# Removed rules must be absent
for removed in GPUTemperatureHigh LoldayGPUVRAMHigh; do
  if echo "$ALERT_NAMES" | grep -qx "$removed"; then
    fail "removed alert '$removed' is still present in chart"
  fi
done
pass "GPUTemperatureHigh + LoldayGPUVRAMHigh removed"

# New rules must be present
for new in DCGMXIDError DCGMThrottleReasonsPersistent GpuSignalFailSafeStuck GpuSignalCountMismatch; do
  echo "$ALERT_NAMES" | grep -qx "$new" \
    || fail "new alert '$new' missing from chart"
done
pass "4 new alerts present (DCGMXIDError, DCGMThrottleReasonsPersistent, GpuSignalFailSafeStuck, GpuSignalCountMismatch)"

# Per-rule severity assertions
get_severity() {
  yq eval ".spec.groups[].rules[] | select(.alert == \"$1\") | .labels.severity" "$TMPDIR/pr.yaml"
}
get_for() {
  yq eval ".spec.groups[].rules[] | select(.alert == \"$1\") | .for" "$TMPDIR/pr.yaml"
}

[ "$(get_severity DCGMXIDError)" = "critical" ] || fail "DCGMXIDError severity must be critical"
[ "$(get_severity DCGMThrottleReasonsPersistent)" = "warning" ] || fail "DCGMThrottleReasonsPersistent severity must be warning"
[ "$(get_severity GpuSignalFailSafeStuck)" = "warning" ] || fail "GpuSignalFailSafeStuck severity must be warning"
[ "$(get_severity GpuSignalCountMismatch)" = "warning" ] || fail "GpuSignalCountMismatch severity must be warning"
[ "$(get_severity TrivyCriticalCVE)" = "warning" ] || fail "TrivyCriticalCVE severity must be warning (demoted)"
[ "$(get_severity PodCrashLoopBackOff)" = "warning" ] || fail "PodCrashLoopBackOff severity must be warning"
[ "$(get_for PodCrashLoopBackOff)" = "15m" ] || fail "PodCrashLoopBackOff for: must be 15m"
pass "severity + for hysteresis correct on key rules"

# --- AlertmanagerConfig ---

yq eval-all '
  select(.kind == "AlertmanagerConfig" and .metadata.name == "discord-receivers")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/amc.yaml"

INHIBIT_COUNT="$(yq eval '.spec.inhibitRules | length' "$TMPDIR/amc.yaml")"
[ "$INHIBIT_COUNT" = "5" ] \
  || fail "expected 5 inhibitRules; got $INHIBIT_COUNT"
pass "5 inhibitRules present"

RECV_COUNT="$(yq eval '.spec.receivers | length' "$TMPDIR/amc.yaml")"
[ "$RECV_COUNT" = "2" ] || fail "expected 2 receivers; got $RECV_COUNT"
pass "2 receivers (discord-critical, discord-warning)"

CRIT_INTERVAL="$(yq eval '.spec.route.routes[0].repeatInterval' "$TMPDIR/amc.yaml")"
WARN_INTERVAL="$(yq eval '.spec.route.routes[1].repeatInterval' "$TMPDIR/amc.yaml")"
[ "$CRIT_INTERVAL" = "4h" ] || fail "critical route repeatInterval must be 4h (got $CRIT_INTERVAL)"
[ "$WARN_INTERVAL" = "24h" ] || fail "warning route repeatInterval must be 24h (got $WARN_INTERVAL)"
pass "per-route repeatIntervals: critical 4h, warning 24h"

CRIT_CONTENT="$(yq eval '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].content // ""' "$TMPDIR/amc.yaml")"
[ "$CRIT_CONTENT" = "@here" ] || fail "discord-critical must use content: @here (got '$CRIT_CONTENT')"
WARN_CONTENT="$(yq eval '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].content // ""' "$TMPDIR/amc.yaml")"
[ -z "$WARN_CONTENT" ] || fail "discord-warning must NOT set content (no @here ping); got '$WARN_CONTENT'"
pass "@here ping policy: critical only"

echo ""
echo "All assertions passed."
```

- [ ] **Step 2: Make executable + verify it runs**

```bash
chmod +x tests/phase7/test_alert_rules_inventory.sh
bash tests/phase7/test_alert_rules_inventory.sh
```

Expected: all `✓` lines + `All assertions passed.`

If any assertion fails, look back at Tasks 3 + 4 and fix the yaml.

- [ ] **Step 3: Commit**

```bash
git add tests/phase7/test_alert_rules_inventory.sh
git commit -m "test(phase7): chart contract test for alert inventory + AlertmanagerConfig

Asserts the 16-alert inventory, the 2 removed rules are absent, the 4
new rules are present with the right severity, PodCrashLoopBackOff has
for: 15m, TrivyCriticalCVE severity is warning, and the
AlertmanagerConfig has 5 inhibitRules + 2 receivers + correct
per-route repeatIntervals + @here policy. Modeled on existing
test_trivy_alert_rule.sh."
```

---

## Task 7: Live smoke shell script (operator-run, post-deploy)

**Files:**

- Create: `tests/2026-05-10-alerting-redesign-smoke.sh`

- [ ] **Step 1: Write the smoke script**

Create `tests/2026-05-10-alerting-redesign-smoke.sh`:

```bash
#!/usr/bin/env bash
# Live smoke for 議題 B (alerting redesign).
# Operator runs after `bash scripts/deploy.sh` has rolled out the new chart
# AND the operator has updated DISCORD_WEBHOOK_URL_WARNING in
# ~/.lolday-secrets.env to point at the new Spidey Warnings channel.
#
# Pre-reqs:
#   - kubectl context points at server30
#   - amtool is on PATH (ships with prometheus toolchain)
#   - port-forward to alertmanager available, or amtool URL configured
set -euo pipefail

# Allow either env-var-driven or port-forward driven access.
AM_URL="${ALERTMANAGER_URL:-http://localhost:9093}"

require() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
require amtool
require kubectl

cleanup() {
  echo "==> cleanup: silence all test alerts"
  amtool --alertmanager.url="$AM_URL" silence query --within=1h --silenced=false 2>/dev/null \
    | awk '/test-alert-/ {print $1}' \
    | xargs -r -n1 amtool --alertmanager.url="$AM_URL" silence expire || true
}
trap cleanup EXIT

echo "==> Test A: critical alert routes to Captain Hook with @here"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayCoreServiceDown" severity="critical" job="backend" \
  annotation:summary="smoke test A — please ignore"
sleep 35  # group_wait + small margin
echo "  Inspect Captain Hook channel for: 🚨 [CRITICAL] LoldayCoreServiceDown + @here"
echo "  Press Enter when confirmed."
read -r

echo "==> Test B: warning alert routes to Spidey Warnings without @here"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="PodCrashLoopBackOff" severity="warning" \
  namespace="lolday" pod="smoke-test-pod" container="x" reason="CrashLoopBackOff" \
  annotation:summary="smoke test B — please ignore"
sleep 35
echo "  Inspect Spidey Warnings channel for: ⚠️ [WARNING] PodCrashLoopBackOff (NO @here)"
echo "  Press Enter when confirmed."
read -r

echo "==> Test C: inhibition — backend down + error rate elevated"
echo "  Adding source alert (LoldayCoreServiceDown)…"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayCoreServiceDown" severity="critical" job="backend" \
  annotation:summary="smoke test C source — please ignore"
sleep 5
echo "  Adding target alert (LoldayBackendErrorRateElevated)…"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayBackendErrorRateElevated" severity="warning" stage="dispatch" \
  annotation:summary="smoke test C target — please ignore"
sleep 35
echo "  Inspect Spidey Warnings: target alert should NOT appear (inhibited)."
echo "  Press Enter when confirmed."
read -r

echo "==> Test D: GpuSignalFailSafeStuck end-to-end"
echo "  This requires Prometheus actually unreachable for 30+ min."
echo "  To simulate quickly: scale kps-prometheus down for ~31 min:"
echo "    kubectl -n monitoring scale --replicas=0 statefulset/kps-prometheus-prometheus"
echo "  Wait 31 minutes (or skip this test — it overlaps with 議題 A's smoke Test D)."
echo "  Press Enter to skip, or wait + observe in Spidey Warnings."
read -r

echo ""
echo "All interactive tests prompted. Cleanup runs automatically."
```

- [ ] **Step 2: Make executable + run shellcheck if available**

```bash
chmod +x tests/2026-05-10-alerting-redesign-smoke.sh
bash -n tests/2026-05-10-alerting-redesign-smoke.sh   # syntax check only
shellcheck tests/2026-05-10-alerting-redesign-smoke.sh || echo "shellcheck not installed; skipping"
```

Do NOT execute the script — it requires a live, deployed cluster.

- [ ] **Step 3: Commit**

```bash
git add tests/2026-05-10-alerting-redesign-smoke.sh
git commit -m "test(smoke): live smoke for alerting redesign

Interactive script — operator runs post-deploy. Tests routing
(critical → Captain Hook + @here; warning → Spidey Warnings, no @here),
inhibition (backend-down suppresses error-rate-elevated), and
fail-safe stuck (overlaps with 議題 A smoke Test D — note in script)."
```

---

## Task 8: Documentation updates

**Files:**

- Modify: `docs/architecture.md`
- Modify: `docs/runbooks/troubleshooting.md`
- Modify: `.claude/rules/charts-and-helm.md`
- Modify: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/reference_discord_channels.md`

- [ ] **Step 1: Add architecture entry**

In `docs/architecture.md`, find §10 (the host-aware GPU signal entry from 議題 A is item 17). Add a new item 18 immediately after:

```markdown
### Alerting redesign (2026-05-10)

Reshapes Discord alerting to follow Google SRE's symptom-based-alerting
model + NVIDIA's gpu-operator fault-detection guidance:

- 16 alerts (12 keep + 4 new; 2 removed). Removed: `GPUTemperatureHigh`,
  `LoldayGPUVRAMHigh` — both fired during normal ML training because they
  treated telemetry (temp / VRAM occupancy) as faults. Replaced by
  `DCGMXIDError` (driver-level fault, critical) and
  `DCGMThrottleReasonsPersistent` (sustained thermal throttle, warning).
  Added `GpuSignalFailSafeStuck` + `GpuSignalCountMismatch` to surface
  silent degradation modes from the host-aware GPU signal (item 17).
- 4 Discord channels (was 3): `Captain Hook` (critical only, @here),
  new `Spidey Warnings` (warning only, no @here), `Spidey heartbeat`
  (DeadMansSwitch, unchanged), `Spidey service-alerts` (backend
  notify\_\*, unchanged).
- 5 inhibition rules suppress predictable cascade (e.g. backend-down
  suppresses error-rate-elevated and volcano-pending-stale).
- Per-route repeatInterval: critical 4h, warning 24h.

`scripts/deploy.sh` already requires both `DISCORD_WEBHOOK_URL_CRITICAL`
and `DISCORD_WEBHOOK_URL_WARNING` env vars; the only operator-side
change is repointing `DISCORD_WEBHOOK_URL_WARNING` at the new channel
(see runbook).

Spec: `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`.
Plan: `docs/superpowers/plans/2026-05-10-alerting-redesign.md`.
```

- [ ] **Step 2: Add troubleshooting SOPs**

Append to `docs/runbooks/troubleshooting.md`:

```markdown
## Symptom: DCGMXIDError fired

**Cause:** NVIDIA driver reported a non-zero XID error code on a GPU.

**Diagnosis:**

1. Note the `gpu` and `Hostname` labels from the Discord alert.
2. SSH to the affected host and run:
```

sudo dmesg | grep -i "NVRM: Xid"

```
3. Match the XID code to https://docs.nvidia.com/deploy/xid-errors/.
Common codes: `13` (graphics engine exception, often app bug), `31`
(GPU memory page fault, often app bug), `48`/`63`/`64`/`74` (uncorrectable
ECC / row remap — hardware degradation, replace card if recurring).
4. Check `dcgmi diag -r 1` (level-1 health check) on the host.

**Mitigation:**

- App-bug-level XIDs (13, 31): may be transient — restart the offending
pod / vcjob. If persistent, investigate the workload.
- Hardware-degradation XIDs: schedule the card for replacement.
Cordon the node; lolday will fail-safe (no dispatch).

## Symptom: GpuSignalFailSafeStuck fired

**Cause:** Backend's host-aware GPU signal (議題 A) has been in fail-safe
mode for 30+ minutes — Prometheus is unreachable.

**Diagnosis:** Same as the existing
"GpuStatusBanner shows 'scheduler in fail-safe mode'" SOP above. This
alert is the 30-min escalation of that condition.

## Symptom: Discord critical channel suddenly noisy from a single incident

**Cause:** Inhibition rule failed to apply.

**Diagnosis:**

1. Inspect the rules:
```

amtool --alertmanager.url=http://localhost:9093 \
 config show | yq eval '.inhibitRules' -

```
2. Confirm 5 inhibitRules are present (see spec §6.2).
3. If a rule is missing or malformed, the chart-side yaml has drifted.
Re-render with `helm template` and compare to the chart source.
```

- [ ] **Step 3: Update the charts-and-helm rule**

Edit `.claude/rules/charts-and-helm.md`. Find the bullet describing the monitoring subfolder:

```
- `alertmanager-rules.yaml` + `alertmanager-config-discord.yaml` — alerting rules + Discord receiver.
```

Replace with:

```
- `alertmanager-rules.yaml` + `alertmanager-config-discord.yaml` — alerting rules + Discord receivers + 5 inhibition rules + per-severity routing. 16 alert rules total (議題 B redesign 2026-05-10). Receivers wire to two distinct Discord channels via Secret keys `webhook-url-critical` (Captain Hook, @here) and `webhook-url-warning` (Spidey Warnings, no @here). See `docs/superpowers/specs/2026-05-10-alerting-redesign-design.md`.
```

- [ ] **Step 4: Update memory file**

Edit `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/reference_discord_channels.md`. Append a fourth bullet for Spidey Warnings — the operator filled in the channel id during Task 1 step 4.

```markdown
- `<NEW-CHANNEL-ID>` — **Spidey Warnings**（議題 B 2026-05-10 alerting redesign 新增）。Alertmanager `severity=warning` 路由到此，無 `@here` ping。讀者可自由閱覽，不會跳出推播。Webhook URL 由 operator 填入 `DISCORD_WEBHOOK_URL_WARNING`。
```

> The operator filled in the channel id during Task 1 step 4. If they have not, leave the placeholder `<NEW-CHANNEL-ID>` and have them update the memory after deploy.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/runbooks/troubleshooting.md .claude/rules/charts-and-helm.md
git commit -m "docs: document alerting redesign (architecture + runbook + rules)"
```

> Memory file is outside the repo — operator updates it manually with their actual channel id; not committed.

---

## Task 9: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend full test suite**

```bash
cd backend && uv run pytest 2>&1 | tail -10
```

Expected: green (≥ 628 tests with the 3 new metric tests from Task 2).

- [ ] **Step 2: Frontend typecheck + tests**

```bash
cd frontend && pnpm typecheck && pnpm test --run 2>&1 | tail -10
```

Expected: clean typecheck, ~283 tests pass (no frontend changes in this PR — just running the regression).

- [ ] **Step 3: Pre-commit on whole repo**

```bash
pre-commit run --all-files 2>&1 | tail -20
```

Expected: all hooks pass. If anything auto-fixes (prettier on docs, etc.), re-stage and commit (do NOT amend; do NOT use --no-verify).

- [ ] **Step 4: Helm lint**

```bash
helm lint charts/lolday 2>&1 | tail -5
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 5: Helm template render (sanity)**

```bash
helm template charts/lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > /tmp/render.yaml && echo "OK: $(wc -l < /tmp/render.yaml) lines rendered"
```

Expected: a multi-thousand-line yaml output, no errors.

- [ ] **Step 6: Run the new chart contract test**

```bash
bash tests/phase7/test_alert_rules_inventory.sh
```

Expected: `All assertions passed.` from Task 6 step 2.

- [ ] **Step 7: Re-run gpu_signal unit tests (sanity)**

```bash
cd backend && uv run pytest tests/services/test_gpu_signal.py -v 2>&1 | tail -10
```

Expected: 19 PASSED (16 from 議題 A + 3 new from Task 2).

- [ ] **Step 8: Commit (only if step 3 modified files)**

If pre-commit auto-fixed anything:

```bash
git add -u
git commit -m "chore: pre-commit auto-fixes"
```

If not, skip.

---

## Task 10: Push + Open PR

**Files:** none

> Branch was created upfront (see "Branch setup" above the File Structure section). All commits from Tasks 2-9 are on `feat/alerting-redesign`.

- [ ] **Step 1: Push**

```bash
git push -u origin feat/alerting-redesign
```

- [ ] **Step 2: Create PR**

```bash
gh pr create --title "feat: alerting redesign — SRE-aligned alerts + 4-channel split" --body "$(cat <<'EOF'
## Summary

Replaces noisy `GPUTemperatureHigh` / `LoldayGPUVRAMHigh` alerts (which
fire during normal ML training, since 2080 Ti's thermal throttle is
85°C and VRAM occupancy 90% is a training default) with NVIDIA's
recommended fault-detection signals (`DCGMXIDError` for driver-level
faults, `DCGMThrottleReasonsPersistent` for sustained thermal throttle).
Splits the Captain Hook channel into critical-only + a new Spidey
Warnings channel (warning-only, no `@here`). Adds 5 inhibition rules
to prevent cascade noise from a single incident. Adds 2 new alerts
that surface silent degradation in the host-aware GPU signal (議題 A):
`GpuSignalFailSafeStuck` (Prom unreachable for 30 min) and
`GpuSignalCountMismatch` (DCGM sees a gpu beyond CLUSTER_PHYSICAL_GPU_COUNT).

Mainstream references: Google SRE Workbook (symptom-based alerting),
NVIDIA DCGM User Guide (XID + throttle reasons as fault signals),
PagerDuty alert-fatigue research, Prometheus alerting best practices.

## What's in this PR

- `feat(metrics)`: expose `lolday_gpu_signal_fail_safe_active` Gauge
- `feat(charts)`: rewrite alert rule inventory (12 keep + 4 new; 2 removed; 2 modified)
- `feat(charts)`: split critical/warning routing (4h vs 24h repeat) + 5 inhibition rules
- `test(promtool)`: unit tests for the 4 new + 2 modified alerts
- `test(phase7)`: chart contract test asserting the 16-alert inventory + AlertmanagerConfig
- `test(smoke)`: live smoke (interactive, operator runs post-deploy)
- `docs`: architecture.md / troubleshooting.md / charts-and-helm rule

## Operator pre-deploy checklist

Before `bash scripts/deploy.sh` after this PR merges:

1. Create `Spidey Warnings` Discord channel
2. Add an incoming webhook to it
3. Update `DISCORD_WEBHOOK_URL_WARNING=...` in `~/.lolday-secrets.env`
   (the variable already exists today; just swap the URL)
4. Note the channel id and add it to memory `reference_discord_channels.md`

## Test plan

- [x] `cd backend && uv run pytest tests/services/test_gpu_signal.py -v` — 19 passed (16 from 議題 A + 3 new metric tests)
- [x] `cd backend && uv run pytest` — green (full suite)
- [x] `cd frontend && pnpm typecheck && pnpm test --run` — green
- [x] `pre-commit run --all-files` — green
- [x] `helm lint charts/lolday` — 0 chart(s) failed
- [x] `bash tests/phase7/test_alert_rules_inventory.sh` — All assertions passed
- [ ] Operator post-merge: complete pre-deploy checklist; deploy; run `tests/2026-05-10-alerting-redesign-smoke.sh`

Spec: docs/superpowers/specs/2026-05-10-alerting-redesign-design.md
Plan: docs/superpowers/plans/2026-05-10-alerting-redesign.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Return the PR URL**

The `gh pr create` output ends with the PR URL (`https://github.com/bolin8017/lolday/pull/N`). Note it for the operator.

---

## Done criteria

- [ ] All 10 tasks above complete
- [ ] `cd backend && uv run pytest` — green
- [ ] `cd frontend && pnpm typecheck && pnpm test --run` — green (no frontend changes; regression check only)
- [ ] `pre-commit run --all-files` — green
- [ ] `helm lint charts/lolday` — green
- [ ] `bash tests/phase7/test_alert_rules_inventory.sh` — `All assertions passed.`
- [ ] PR opened with body including `Spec:` and `Plan:` references
- [ ] Operator has completed Task 1 pre-deploy checklist
- [ ] Operator has run `tests/2026-05-10-alerting-redesign-smoke.sh` and confirmed Tests A–C interactively (Test D may be skipped or overlapped with 議題 A's smoke Test D)
