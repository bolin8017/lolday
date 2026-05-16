# Monitoring NP gaps + collateral alert recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the NetworkPolicy design gaps introduced by chart 0.24.0 (PR #179) that cause 16 currently-firing Prometheus / Alertmanager alerts (KubeletDown + 5×LoldayCoreServiceDown + 6×TargetDown + GpuSignalFailSafeStuck + KubeJobNotCompleted), restore the pg-backup nightly CronJob, and align the Spidey Heartbeat documentation with the actual SRE dead-man-switch implementation.

**Architecture:**

- `monitoring-default-egress` is missing three mainstream allows (same-ns, kubelet on host network, and the comment about "cross-ns ingress from lolday is not needed" is wrong because backend→prom is _both_ lolday-egress and monitoring-ingress). Fix by adding the missing egress rule + a separate `prometheus-from-lolday-backend` ingress NP.
- `harbor-internal-ingress-allow` and the postgres-exporter pod have no path for Prometheus to scrape them. Add two narrow ingress NPs in `lolday` ns that mirror the existing `backend-metrics-from-monitoring-only` pattern.
- `pg-backup` CronJob hangs forever because the upstream `prodrigestivill/postgres-backup-local` image only performs a backup at startup when `BACKUP_ON_START=TRUE`; without it the container idles waiting for an HTTP trigger and the K8s Job never completes.
- `docs/operations.md` describes Spidey Heartbeat as a positive-heartbeat channel, but `check.py` only POSTs on Watchdog failure (SRE pattern). Fix the docs; file positive-heartbeat as architecture §10 tech debt.

**Tech Stack:** Helm 3, K8s `networking.k8s.io/v1`, kube-router NP enforcement (K3s), `prodrigestivill/postgres-backup-local` env contract.

**Source of evidence (do not duplicate investigation):**

- `kubectl -n monitoring exec prometheus-kps-prometheus-0 -c prometheus -- wget -qO- 'http://localhost:9090/api/v1/targets?state=any'` showed kubelet scrape `lastError: dial tcp 140.118.155.30:10250: connect: connection refused` (NP REJECT signature per auto-memory `k3s_np_enforcement_reject.md`).
- `monitoring-default-egress` ipBlock rule allows only 443+6443 on `0.0.0.0/0`; port 10250 is not whitelisted, so monitoring → host-network kubelet is denied even though the chart comment said `kube-system namespaceSelector` would cover kubelet (kubelet has no pod label — that selector cannot match host-network targets).
- The Captain Hook spam (`@here` every 5 min) is dominated by `KubeletDown` repeat (`repeatInterval: 4h` per the `discord-critical` route — wait, evidence shows actual cadence is 5-min; this is because Alertmanager's `groupInterval: 5m` is what controls follow-up notifications when _new_ alerts join the group, and we have 7 different critical alerts flapping in).
- `deadmans-switch.yaml` wires `DISCORD_URL` → `alertmanager-discord/webhook-url-critical` (Captain Hook). `check.py` POSTs only on Watchdog failure → silence on success → Spidey Heartbeat empty since 2026-05-10.
- `pg-backup` pod `pg-backup-29647860-2grzc` has been Running 18h with only three log lines after startup; the upstream image's README ("Run the backup at startup" section) requires `BACKUP_ON_START=TRUE` for one-shot runs.

---

## File structure

| Path                                                                                       | Action | Responsibility                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------ | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `charts/lolday/templates/monitoring/netpol-default-deny.yaml`                              | modify | Add same-ns egress + kubelet ipBlock egress to `monitoring-default-egress`; add new `prometheus-from-lolday-backend` NP for cross-ns ingress                                                                                          |
| `charts/lolday/templates/network-policies/harbor-metrics-from-monitoring.yaml`             | create | Allow monitoring/prometheus → harbor pods on metrics port 8001                                                                                                                                                                        |
| `charts/lolday/templates/network-policies/postgres-exporter-metrics-from-monitoring.yaml`  | create | Allow monitoring/prometheus → postgres-exporter on port 9187                                                                                                                                                                          |
| `charts/lolday/templates/monitoring/pg-backup-cronjob.yaml`                                | modify | Add `BACKUP_ON_START: "TRUE"` env so the upstream image actually runs a backup and exits                                                                                                                                              |
| `charts/lolday/Chart.yaml`                                                                 | modify | Bump version + appVersion `0.24.0 → 0.24.1`                                                                                                                                                                                           |
| `charts/lolday/tests/network-policies/monitoring-default-egress_test.yaml`                 | create | helm-unittest suite asserting same-ns + kubelet egress + DNS + cross-ns rules render                                                                                                                                                  |
| `charts/lolday/tests/network-policies/prometheus-from-lolday-backend_test.yaml`            | create | helm-unittest suite asserting the ingress NP renders for backend pod selector                                                                                                                                                         |
| `charts/lolday/tests/network-policies/harbor-metrics-from-monitoring_test.yaml`            | create | helm-unittest suite                                                                                                                                                                                                                   |
| `charts/lolday/tests/network-policies/postgres-exporter-metrics-from-monitoring_test.yaml` | create | helm-unittest suite                                                                                                                                                                                                                   |
| `docs/operations.md`                                                                       | modify | Spidey Heartbeat description — replace "Messages mean healthy; absence is the anomaly" with the actual SRE dead-man-switch behaviour (POST on failure → Captain Hook; channel currently unused, pending positive-heartbeat follow-up) |
| `docs/architecture.md`                                                                     | modify | Update §5.2 Discord webhook list to reflect deadmans-switch wires to `webhook-url-critical` today; add §10 tech debt entries (positive-heartbeat follow-up + Trivy 6 CRITICAL CVE images)                                             |
| `.claude/rules/charts-and-helm.md`                                                         | modify | Bump chart version reference `0.24.1`-baseline note (was anticipatorily set; reconcile to reality)                                                                                                                                    |

---

## Pre-flight (must complete before Task 1)

- [ ] **PF-1: Confirm cluster state and capture pre-fix metrics**

Run:

```bash
kubectl -n monitoring exec prometheus-kps-prometheus-0 -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/alerts' | \
  python3 -c "import sys,json;a=json.load(sys.stdin)['data']['alerts'];print(f'firing={len([x for x in a if x[\"state\"]==\"firing\"])}'); [print(' ',x['labels'].get('alertname'),x['labels'].get('severity')) for x in a if x['state']=='firing']"
```

Expected: 7 critical (`KubeletDown`, 5×`LoldayCoreServiceDown`, `Watchdog`) + 8 warning (`TargetDown`×6, `GpuSignalFailSafeStuck`, `TrivyCriticalCVE`, `KubeJobNotCompleted`). Write the count to a local scratch note for post-fix comparison.

- [ ] **PF-2: Enter isolated worktree**

The user authorised worktree use. Use `EnterWorktree` with `name: fix-monitoring-np-alerts` (the executing-plans skill takes care of base-ref).

---

## Task 1: Extend `monitoring-default-egress` with same-ns + kubelet host rules

**Files:**

- Modify: `charts/lolday/templates/monitoring/netpol-default-deny.yaml:92-148` (the `monitoring-default-egress` NP body)

- [ ] **Step 1: Update the chart comment block on the egress NP**

Replace the comment header (currently at lines 78-80, "Egress: DNS + scrape targets across the three workload namespaces + kube-apiserver (Prometheus kube-state-metrics, kubelet metrics) + external (Alertmanager → Discord webhook).") with:

```yaml
# Egress for the monitoring ns. Allow:
#   - DNS (CoreDNS)
#   - Same-namespace egress (Prom → AM, Grafana → Prom, deadmans-switch → AM).
#     Pods inside the same ns are NOT auto-allowed; selecting `podSelector: {}`
#     on the deny rule covers them, so a same-ns allow is required.
#   - Cross-ns scrape egress to the workload namespaces (lolday, lolday-jobs,
#     kube-system, trivy-system, gpu-operator). namespaceSelector matches
#     pod-network targets only.
#   - Host-network egress to kubelet on TCP 10250. Kubelet runs on the K3s
#     node's host network and has no pod label, so namespaceSelector(kube-system)
#     can NOT match it. Use an ipBlock with the K3s pod+service CIDR carve-out.
#   - External egress to 0.0.0.0/0 on 443 (Alertmanager → Discord webhook) and
#     6443 (kube-apiserver reachable via the node IP after kube-proxy DNAT).
```

- [ ] **Step 2: Insert the same-namespace egress rule**

In `netpol-default-deny.yaml`, immediately after the DNS egress rule (currently ends with `- { protocol: TCP, port: 53 }` around line 101), add:

```yaml
# Same-namespace egress (Prom → AM, Grafana → Prom, deadmans-switch → AM,
# AM → AM cluster gossip on :9094 if HA, etc.).
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: { { .Values.monitoring.namespace } }
```

- [ ] **Step 3: Insert the kubelet ipBlock egress rule**

Immediately before the existing `# External egress for Alertmanager Discord webhook.` comment (currently around line 133), add:

```yaml
# Kubelet metrics on the K3s node's host network. The kube-system
# namespaceSelector above cannot match host-network targets (kubelet has
# no pod label), so we open TCP 10250 via ipBlock with the pod+service
# CIDR carve-out. Mainstream kube-prometheus-stack pattern; see
# https://github.com/prometheus-community/helm-charts/blob/main/charts/kube-prometheus-stack/values.yaml
# `prometheus.networkPolicy.egress` for the upstream equivalent.
- to:
    - ipBlock:
        cidr: 0.0.0.0/0
        except:
          - 10.42.0.0/16 # K3s pod CIDR
          - 10.43.0.0/16 # K3s service CIDR
          - 192.168.0.0/16
          - 172.16.0.0/12
          - 169.254.0.0/16
  ports:
    - { protocol: TCP, port: 10250 }
```

- [ ] **Step 4: Render and verify the change with helm template**

Run:

```bash
helm template charts/lolday \
  --show-only templates/monitoring/netpol-default-deny.yaml \
  | grep -A 200 'name: monitoring-default-egress' | head -80
```

Expected: the egress list now contains 7 entries (DNS, same-ns, lolday, lolday-jobs, kube-system, trivy-system, gpu-operator, kubelet ipBlock, external 443/6443 ipBlock) and the kubelet rule shows `port: 10250` with the CIDR exclusion list.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/monitoring/netpol-default-deny.yaml
git commit -m "fix(charts): allow monitoring same-ns + kubelet host-network egress

The 0.24.0 monitoring-default-egress NP is missing two mainstream allows:
  - Same-namespace egress: pods inside a ns selected by podSelector:{} are NOT
    auto-allowed when policyTypes includes Egress. Prom→AM, Grafana→Prom, and
    deadmans-switch→AM all need it.
  - Kubelet metrics on host network: kubelet has no pod label, so the
    kube-system namespaceSelector cannot match it. Open TCP 10250 via an
    ipBlock with the K3s pod+service CIDR carve-out.

Fixes KubeletDown + the deadmans-switch \"Watchdog missing\" Discord spam
(deadmans-switch could not reach kps-alertmanager.monitoring:9093).

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 2: Add `prometheus-from-lolday-backend` ingress NP

**Files:**

- Modify: `charts/lolday/templates/monitoring/netpol-default-deny.yaml` (append a new `---` block at the end before `{{- end }}`)

- [ ] **Step 1: Append the new NP**

Inside the `{{- if .Values.monitoring.enabled }}` … `{{- end }}` block, before the final `{{- end }}`, append:

```yaml
---
# Cross-ns ingress: allow backend pods in `lolday` ns to query Prometheus
# (kps-prometheus.monitoring.svc:9090). The 2026-05-11 host-aware GPU
# signal design (docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md
# §7) made this a fail-closed runtime path — without ingress the scheduler
# pages GpuSignalFailSafeStuck. Backend does NOT need Alertmanager API
# access, so port 9090 only.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: prometheus-from-lolday-backend
  namespace: { { .Values.monitoring.namespace } }
  labels: { { - include "lolday.labels" . | nindent 4 } }
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: prometheus
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: { { .Values.global.namespace } }
          podSelector:
            matchLabels:
              app.kubernetes.io/component: backend
      ports:
        - { protocol: TCP, port: 9090 }
```

- [ ] **Step 2: Render and verify**

```bash
helm template charts/lolday \
  --show-only templates/monitoring/netpol-default-deny.yaml \
  | grep -A 25 'name: prometheus-from-lolday-backend'
```

Expected: the rendered NP has `podSelector: app.kubernetes.io/name: prometheus` and an ingress block citing `kubernetes.io/metadata.name: lolday` + `app.kubernetes.io/component: backend` + `port: 9090`.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/monitoring/netpol-default-deny.yaml
git commit -m "fix(charts): allow backend → prometheus ingress in monitoring ns

The chart comment claimed cross-ns ingress from lolday was not needed
because backend reads Prometheus via the in-cluster Service. The
reasoning is wrong — that traffic is BOTH lolday-egress AND monitoring-
ingress, and the monitoring-default-deny + monitoring-internal-ingress
combination blocks it.

Add a narrow ingress NP for backend pod → kps-prometheus:9090 only.
Closes the GpuSignalFailSafeStuck root cause (scheduler fail-closed
because gpu_signal could not reach Prometheus).

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 3: Add `harbor-metrics-from-monitoring` ingress NP

**Files:**

- Create: `charts/lolday/templates/network-policies/harbor-metrics-from-monitoring.yaml`

- [ ] **Step 1: Create the file**

```yaml
{{- if and .Values.harbor.enabled .Values.monitoring.enabled }}
{{/* Allow kps-prometheus to scrape Harbor metrics endpoints (:8001 on
     harbor-core / harbor-exporter / harbor-jobservice). The existing
     `harbor-internal-ingress-allow` only allows in-Harbor pod-to-pod
     traffic (app=harbor → app=harbor); Prometheus carries a different
     label set and is in another namespace.

     Mirrors the `backend-metrics-from-monitoring-only` pattern.

     Closes the 0.24.0 NP-hardening regression that fired TargetDown
     {job=harbor} + LoldayCoreServiceDown {job=harbor}. */}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: harbor-metrics-from-monitoring-only
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app: harbor
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.monitoring.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/name: prometheus
      ports:
        - { protocol: TCP, port: 8001 }
{{- end }}
```

- [ ] **Step 2: Verify render**

```bash
helm template charts/lolday \
  --show-only templates/network-policies/harbor-metrics-from-monitoring.yaml
```

Expected: a single NetworkPolicy with `name: harbor-metrics-from-monitoring-only`, `namespace: lolday`, `podSelector: app=harbor`, ingress from `monitoring/prometheus` on 8001.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/network-policies/harbor-metrics-from-monitoring.yaml
git commit -m "fix(charts): allow monitoring/prometheus → harbor metrics :8001

Closes TargetDown{job=harbor} + LoldayCoreServiceDown{job=harbor} after
the 0.24.0 lolday-default-deny-ingress NP hardening removed implicit
cross-ns allows. Mirrors the backend-metrics-from-monitoring-only
pattern (one-way ingress, single port, exact pod label).

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 4: Add `postgres-exporter-metrics-from-monitoring` ingress NP

**Files:**

- Create: `charts/lolday/templates/network-policies/postgres-exporter-metrics-from-monitoring.yaml`

- [ ] **Step 1: Create the file**

```yaml
{{- if and .Values.postgresql.enabled .Values.monitoring.enabled .Values.monitoring.postgresExporter.enabled }}
{{/* Allow kps-prometheus to scrape postgres-exporter at :9187.
     Lives outside the postgresql-ingress-allow NP because that one
     targets postgresql (the DB pod), not postgres-exporter.

     Mirrors the `backend-metrics-from-monitoring-only` pattern. */}}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: postgres-exporter-metrics-from-monitoring-only
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: postgres-exporter
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.monitoring.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/name: prometheus
      ports:
        - { protocol: TCP, port: 9187 }
{{- end }}
```

- [ ] **Step 2: Verify render**

```bash
helm template charts/lolday \
  --show-only templates/network-policies/postgres-exporter-metrics-from-monitoring.yaml
```

Expected: one NP named `postgres-exporter-metrics-from-monitoring-only` selecting `app.kubernetes.io/name: postgres-exporter` with ingress from monitoring/prometheus on 9187.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/network-policies/postgres-exporter-metrics-from-monitoring.yaml
git commit -m "fix(charts): allow monitoring/prometheus → postgres-exporter :9187

Closes TargetDown{job=postgres-exporter} + LoldayCoreServiceDown{job=
postgres-exporter} from the 0.24.0 hardening regression.

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 5: Fix `pg-backup` `BACKUP_ON_START` to make CronJob complete

**Files:**

- Modify: `charts/lolday/templates/monitoring/pg-backup-cronjob.yaml:85-86`

- [ ] **Step 1: Update the env block + comment**

Replace the existing `SCHEDULE: "@disabled"` env block (currently lines 81-86 — the lines starting at `# @disabled hands scheduling to the K8s CronJob`) with:

```yaml
# @disabled hands scheduling to the K8s CronJob — the image
# would otherwise spawn its own cron daemon. See
# github.com/prodrigestivill/docker-postgres-backup-local
# README "Crontab schedule".
- name: SCHEDULE
  value: "@disabled"
# When the K8s CronJob fires, run a single backup on container
# startup, then exit so the Job completes. Without this the
# container idles on the health-check listener forever and
# KubeJobNotCompleted fires. See the upstream README
# "Run the backup at startup" section.
- name: BACKUP_ON_START
  value: "TRUE"
```

- [ ] **Step 2: Render and verify**

```bash
helm template charts/lolday \
  --show-only templates/monitoring/pg-backup-cronjob.yaml \
  | grep -B 1 -A 1 'BACKUP_ON_START'
```

Expected output contains:

```
                - name: BACKUP_ON_START
                  value: "TRUE"
```

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/monitoring/pg-backup-cronjob.yaml
git commit -m "fix(charts): pg-backup needs BACKUP_ON_START=TRUE to actually run

The prodrigestivill/postgres-backup-local image, when SCHEDULE=@disabled,
only performs a backup at startup when BACKUP_ON_START=TRUE; otherwise it
idles waiting for an HTTP trigger and the K8s Job never completes. Without
it, the nightly CronJob's pod sat Running 18h+, firing KubeJobNotCompleted
and never actually backing up.

Verified upstream contract:
  https://github.com/prodrigestivill/docker-postgres-backup-local#run-the-backup-at-startup

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 6: Add helm-unittest suite for the new + modified NPs

**Files:**

- Create: `charts/lolday/tests/network-policies/monitoring-default-egress_test.yaml`
- Create: `charts/lolday/tests/network-policies/prometheus-from-lolday-backend_test.yaml`
- Create: `charts/lolday/tests/network-policies/harbor-metrics-from-monitoring_test.yaml`
- Create: `charts/lolday/tests/network-policies/postgres-exporter-metrics-from-monitoring_test.yaml`

- [ ] **Step 1: Inspect the helm-unittest pattern already in the repo**

Run:

```bash
ls charts/lolday/tests/ 2>/dev/null && find charts/lolday/tests -name '*_test.yaml' | head -10
```

Expected: the directory exists (`tests/network-policies/`, `tests/monitoring/`, etc.) and at least one `*_test.yaml` file follows the helm-unittest schema (`suite:`, `templates:`, `tests:` with `it:` + `asserts:`). If the directory does not exist, list it as a deviation and skip Task 6.

- [ ] **Step 2: Write the `monitoring-default-egress` suite**

```yaml
# charts/lolday/tests/network-policies/monitoring-default-egress_test.yaml
suite: monitoring-default-egress
templates:
  - monitoring/netpol-default-deny.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: opens same-ns egress to monitoring
    asserts:
      - documentKind:
          of: NetworkPolicy
          name: monitoring-default-egress
      - contains:
          path: spec.egress
          content:
            to:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: monitoring
  - it: opens kubelet host-network egress on 10250 with K3s CIDR carve-out
    asserts:
      - contains:
          path: spec.egress
          content:
            to:
              - ipBlock:
                  cidr: 0.0.0.0/0
                  except:
                    - 10.42.0.0/16
                    - 10.43.0.0/16
                    - 192.168.0.0/16
                    - 172.16.0.0/12
                    - 169.254.0.0/16
            ports:
              - protocol: TCP
                port: 10250
  - it: retains existing scrape-target ns allows
    asserts:
      - contains:
          path: spec.egress
          content:
            to:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: lolday
      - contains:
          path: spec.egress
          content:
            to:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: kube-system
```

- [ ] **Step 3: Write the `prometheus-from-lolday-backend` suite**

```yaml
suite: prometheus-from-lolday-backend
templates:
  - monitoring/netpol-default-deny.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: renders the ingress NP scoped to backend → prom :9090
    asserts:
      - documentKind:
          of: NetworkPolicy
          name: prometheus-from-lolday-backend
      - equal:
          path: spec.podSelector.matchLabels.app\.kubernetes\.io/name
          value: prometheus
      - contains:
          path: spec.ingress
          content:
            from:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: lolday
                podSelector:
                  matchLabels:
                    app.kubernetes.io/component: backend
            ports:
              - protocol: TCP
                port: 9090
```

- [ ] **Step 4: Write the `harbor-metrics-from-monitoring` suite**

```yaml
suite: harbor-metrics-from-monitoring
templates:
  - network-policies/harbor-metrics-from-monitoring.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: allows ingress only from monitoring/prometheus on :8001
    asserts:
      - documentKind:
          of: NetworkPolicy
          name: harbor-metrics-from-monitoring-only
      - equal:
          path: spec.podSelector.matchLabels.app
          value: harbor
      - contains:
          path: spec.ingress
          content:
            from:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: monitoring
                podSelector:
                  matchLabels:
                    app.kubernetes.io/name: prometheus
            ports:
              - protocol: TCP
                port: 8001
  - it: is gated by .Values.harbor.enabled and .Values.monitoring.enabled
    set:
      harbor.enabled: false
    asserts:
      - hasDocuments:
          count: 0
```

- [ ] **Step 5: Write the `postgres-exporter-metrics-from-monitoring` suite**

```yaml
suite: postgres-exporter-metrics-from-monitoring
templates:
  - network-policies/postgres-exporter-metrics-from-monitoring.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: allows ingress only from monitoring/prometheus on :9187
    asserts:
      - documentKind:
          of: NetworkPolicy
          name: postgres-exporter-metrics-from-monitoring-only
      - equal:
          path: spec.podSelector.matchLabels.app\.kubernetes\.io/name
          value: postgres-exporter
      - contains:
          path: spec.ingress
          content:
            from:
              - namespaceSelector:
                  matchLabels:
                    kubernetes.io/metadata.name: monitoring
                podSelector:
                  matchLabels:
                    app.kubernetes.io/name: prometheus
            ports:
              - protocol: TCP
                port: 9187
```

- [ ] **Step 6: Run helm-unittest**

```bash
cd charts/lolday && helm unittest -f 'tests/network-policies/*_test.yaml' . 2>&1 | tail -30
```

Expected: 4 suites pass with the assertions listed above. If `helm unittest` is not on PATH, install via `helm plugin install https://github.com/helm-unittest/helm-unittest` first. If suites fail because the assertion DSL differs from what is shown, adapt the `path:` / `content:` syntax to match the existing test files' style and rerun.

- [ ] **Step 7: Commit**

```bash
git add charts/lolday/tests/network-policies/
git commit -m "test(charts): helm-unittest suites for monitoring NP fixes

Covers the four NetworkPolicy changes from Tasks 1-4:
  - monitoring-default-egress (same-ns + kubelet ipBlock)
  - prometheus-from-lolday-backend (cross-ns ingress for gpu_signal)
  - harbor-metrics-from-monitoring-only (scrape ingress)
  - postgres-exporter-metrics-from-monitoring-only (scrape ingress)

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 7: Update Spidey Heartbeat documentation to match implementation

**Files:**

- Modify: `docs/operations.md:14-21` (the Discord channels table — replace the Spidey Heartbeat row description)
- Modify: `docs/operations.md:27-32` (the webhook env mapping table — clarify `DISCORD_URL` is currently the failure-only sink)
- Modify: `docs/operations.md:34-39` (debug entry points — replace the heartbeat debug step)
- Modify: `docs/architecture.md` — find the Discord webhook list in §5 / §Discord and reconcile

- [ ] **Step 1: Rewrite the Spidey Heartbeat row in `docs/operations.md`**

Replace the row (currently `| Spidey Heartbeat      | \`1495780321239502919\` | \`deadmans-switch\` CronJob (...) | Messages mean healthy; absence is the anomaly |`) with:

```markdown
| Spidey Heartbeat | `1495780321239502919` | _Currently unused._ Reserved for the future positive-heartbeat follow-up (architecture.md §10) | Empty channel today |
```

- [ ] **Step 2: Rewrite the webhook env mapping table**

Replace the existing table (around lines 27-32) with:

```markdown
| Env var                        | Channel               | Current consumer                                                                                   |
| ------------------------------ | --------------------- | -------------------------------------------------------------------------------------------------- |
| `DISCORD_WEBHOOK_URL_CRITICAL` | Captain Hook          | Alertmanager `severity=critical` + deadmans-switch failure                                         |
| `DISCORD_WEBHOOK_URL_WARNING`  | Spidey Warnings       | Alertmanager `severity=warning`                                                                    |
| `DISCORD_WEBHOOK_URL_EVENTS`   | Spidey Service Alerts | backend `services/discord.py`                                                                      |
| `DISCORD_URL` (CronJob only)   | Captain Hook _today_  | deadmans-switch on Watchdog-fail (was originally intended for Spidey Heartbeat; see §10 tech debt) |
```

- [ ] **Step 3: Rewrite the debug entries**

Replace the "Spidey Heartbeat drops out" bullet with:

```markdown
- Spidey Heartbeat empty → expected today. The current `deadmans-switch` is the SRE dead-man-switch pattern (POST on monitoring-chain failure only). The channel exists so an operator can wire a positive-heartbeat follow-up; tracked in `docs/architecture.md` §10.
- Captain Hook firing every 5 min with no real outage → check `kubectl -n monitoring logs job/$(kubectl -n monitoring get jobs -l app.kubernetes.io/name=deadmans-switch -o name | tail -1)` for `Alertmanager unreachable` (NetworkPolicy regression — see plans/2026-05-16-monitoring-np-and-alerts-recovery.md).
```

- [ ] **Step 4: Update `docs/architecture.md`**

Search the file for the four-channel webhook description (`docs/architecture.md` §5, around the "Discord webhooks (× 4 channels)" sentence) and rewrite it to match Step 2's table — drop the "deadmans-switch heartbeat (`DISCORD_URL` on the CronJob env → Spidey Heartbeat; independent secret)" claim and replace with "deadmans-switch failure pings (`DISCORD_URL` on the CronJob env → Captain Hook today; positive heartbeat pending, §10)".

- [ ] **Step 5: Verify the operations.md table renders correctly**

Run:

```bash
grep -A 6 'Spidey Heartbeat' docs/operations.md | head -12
```

Expected: the new row "_Currently unused._ Reserved for the future positive-heartbeat follow-up" appears.

- [ ] **Step 6: Commit**

```bash
git add docs/operations.md docs/architecture.md
git commit -m "docs: align Spidey Heartbeat description with deadmans-switch impl

operations.md described Spidey Heartbeat as 'Messages mean healthy;
absence is the anomaly' (positive-heartbeat pattern), but
deadmans-switch (charts/lolday/files/deadmans_switch/check.py) actually
POSTs only on Watchdog failure, to the critical webhook (Captain Hook).
The mismatch had Spidey Heartbeat sitting empty for 6 days while
operators believed it was monitored.

Document the implementation accurately and file positive-heartbeat as
§10 tech debt (architecture.md update in next commit).

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 8: Add tech-debt entries to `docs/architecture.md` §10

**Files:**

- Modify: `docs/architecture.md` §10 (the rolling tech-debt ledger)

- [ ] **Step 1: Locate the tech-debt ledger anchor**

Run:

```bash
grep -n '^## 10\.' docs/architecture.md | head -3
```

Note the next available item number. Use that as the seed for the entries below.

- [ ] **Step 2: Append two new entries**

Add two new numbered items to §10 (use the next two integers after the existing maximum):

```markdown
### N. Spidey Heartbeat positive-heartbeat (deferred from PR fixing monitoring NP regression, 2026-05-16)

The `Spidey Heartbeat` Discord channel has been silent since the 2026-05-10 alerting redesign because `deadmans-switch` is the SRE dead-man-switch pattern (failure-only POST to critical). To restore the positive-heartbeat semantics described in the 2026-05-10 spec, do:

1. Operator provisions a Spidey Heartbeat webhook URL (Discord channel → Integrations → Create Webhook).
2. Add `DISCORD_WEBHOOK_URL_HEARTBEAT` to `~/.lolday-secrets.env` and `.lolday-secrets.env.example`.
3. Extend `scripts/deploy.sh` to push it into the `alertmanager-discord` Secret as a new key `webhook-url-heartbeat`.
4. Extend `charts/lolday/templates/monitoring/deadmans-switch.yaml` with a second env `DISCORD_HEARTBEAT_URL` referencing the new Secret key.
5. Extend `charts/lolday/files/deadmans_switch/check.py` so that `main()` POSTs a short success ping to `DISCORD_HEARTBEAT_URL` after a successful Watchdog check; skip with a `print()` log line if the env is unset (graceful degrade so the operator can stage step 1 separately from the chart bump).
6. Add unit coverage in `backend/tests/integration/services/test_deadmans_switch_check.py`.

Driver: operator preference. Effort: ~1 PR. Risk: low (gracefully degrades).

### N+1. Trivy CRITICAL CVE backlog across 6 cluster images (2026-05-16)

Trivy Operator currently reports 6 images with CRITICAL CVEs:

- `prodrigestivill/postgres-backup-local:16` — 6 CVE (most likely glibc / openssl in the Debian base)
- `goharbor/nginx-photon:v2.15.0` — 1 CVE
- `goharbor/harbor-portal:v2.15.0` — 1 CVE
- `goharbor/harbor-db:v2.15.0` — 1 CVE
- `goharbor/redis-photon:v2.15.0` — 1 CVE
- `minio/minio:RELEASE.2024-12-18T13-15-44Z` — 1 CVE

Each requires independent CVE → fix-version research (Harbor releases are coupled; MinIO and pg-backup are independent). Track via the Trivy dashboard; refresh image digests in `values.yaml` once an upstream tag with the fix lands. Driver: vuln-scan dashboard. Effort: 1 PR per image family.
```

(Replace `N` and `N+1` with the actual next integers.)

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: §10 add positive-heartbeat + Trivy CRITICAL CVE backlog tech debt

Two entries deferred from the monitoring-NP-recovery PR:
  - Positive heartbeat for Spidey Heartbeat (6-step follow-up)
  - 6 image families with CRITICAL CVEs (per-image PRs)

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 9: Bump chart version 0.24.0 → 0.24.1

**Files:**

- Modify: `charts/lolday/Chart.yaml:5,9` (version + appVersion)
- Modify: `.claude/rules/charts-and-helm.md` (the line that names the current chart version)

- [ ] **Step 1: Bump Chart.yaml**

Edit lines 5 and 9:

```yaml
version: 0.24.1
# ...
appVersion: "0.24.1"
```

- [ ] **Step 2: Update the project rule**

Search `.claude/rules/charts-and-helm.md` for the line referencing the current chart version (it currently says `both currently \`0.24.1\``). Verify the wording still matches the post-PR reality; if it already says `0.24.1`, no edit is needed.

- [ ] **Step 3: Re-run helm lint**

```bash
helm lint charts/lolday
```

Expected: `0 chart(s) failed` (warnings about `icon` are OK).

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/Chart.yaml .claude/rules/charts-and-helm.md
git commit -m "chore(charts): bump 0.24.0 → 0.24.1 (monitoring NP + pg-backup hotfix)

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 10: Full chart sanity (lint + template + unittest)

- [ ] **Step 1: helm lint**

```bash
helm lint charts/lolday
```

Expected: 1 chart linted, 0 failed.

- [ ] **Step 2: helm template (catches macro/typo bugs)**

```bash
helm template charts/lolday > /tmp/lolday-rendered.yaml && wc -l /tmp/lolday-rendered.yaml
```

Expected: prints a line count in the thousands; no Go template parse errors on stderr.

- [ ] **Step 3: helm-unittest full run**

```bash
cd charts/lolday && helm unittest .
```

Expected: all suites pass. If any pre-existing suite fails because of the new resources (e.g., a `hasDocuments:` count test), update those suites in this same task (do NOT skip them).

- [ ] **Step 4: pre-commit run**

```bash
pre-commit run --all-files
```

Expected: all hooks pass. If a hook fails (formatting, image-digest-pin, etc.), fix at the source — do NOT `--no-verify`.

- [ ] **Step 5: Commit if pre-commit changed anything**

```bash
git status --short
# If any files appear with status M:
git add -A
git commit -m "chore: pre-commit fixups for chart hotfix branch

Refs: docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md"
```

---

## Task 11: Deploy the chart to the live cluster and verify alerts clear

This task uses `kubectl` + `helm upgrade` only — no sudo needed (per CLAUDE.md `kubectl_no_sudo_is_mine.md`).

- [ ] **Step 1: helm upgrade with values carry-over discipline**

Run:

```bash
helm upgrade lolday charts/lolday -n lolday --reuse-values --wait --timeout 10m
```

Expected: `STATUS: deployed`, `REVISION: 180`. If the upgrade fails on `context deadline exceeded` (as it did at helm rev 171 and 177), re-run with `--timeout 20m`.

> Note: `--reuse-values` keeps the operator-injected values from rev 179 (helper image digests, secrets, etc.) — same discipline as auto-memory `feedback_helm_upgrade_state_carry.md`. If a future operator deletes a chart value, switch to `--reset-then-reuse-values` instead.

- [ ] **Step 2: Verify the new + modified NetworkPolicies are applied**

```bash
kubectl -n monitoring get networkpolicy
kubectl -n lolday get networkpolicy | grep -E '(harbor-metrics-from|postgres-exporter-metrics-from)'
kubectl -n monitoring get networkpolicy monitoring-default-egress -o yaml | grep -A 5 -E '(ipBlock|10250|namespaceSelector)'
```

Expected:

- `monitoring-default-egress` now contains a `10250` port and a same-ns `monitoring` namespaceSelector.
- `prometheus-from-lolday-backend` exists in `monitoring` ns.
- `harbor-metrics-from-monitoring-only` and `postgres-exporter-metrics-from-monitoring-only` exist in `lolday` ns.

- [ ] **Step 3: Wait for Prometheus to re-scrape (one minute) and verify targets**

```bash
sleep 60
PROM=$(kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheus -o name | head -1)
kubectl -n monitoring exec "$PROM" -c prometheus -- wget -qO- 'http://localhost:9090/api/v1/targets?state=active' \
  | python3 -c "import sys,json;t=json.load(sys.stdin)['data']['activeTargets'];d=[x for x in t if x['health']!='up'];print(f'down/unknown: {len(d)}');[print(f'  {x[\"labels\"].get(\"job\")} {x[\"scrapeUrl\"]} {x[\"health\"]}') for x in d]"
```

Expected: kubelet, harbor (×3 endpoints), postgres-exporter targets all show `up`. If any is still `down`, capture the `lastError` and STOP — investigate before continuing.

- [ ] **Step 4: Verify alerts in Alertmanager have cleared**

```bash
AM=$(kubectl -n monitoring get pods -l app.kubernetes.io/name=alertmanager -o name | head -1)
kubectl -n monitoring exec "$AM" -c alertmanager -- wget -qO- 'http://localhost:9093/api/v2/alerts?active=true' \
  | python3 -c "import sys,json;a=json.load(sys.stdin);print(f'active alerts: {len(a)}');[print(f'  {x[\"labels\"].get(\"alertname\")} severity={x[\"labels\"].get(\"severity\")}') for x in a]"
```

Expected: `KubeletDown`, `TargetDown` ×6, `LoldayCoreServiceDown` ×5, `GpuSignalFailSafeStuck` all gone within 5-10 minutes of the helm upgrade. `Watchdog` (severity=none, intended) and `TrivyCriticalCVE` (deferred) may remain. If `KubeJobNotCompleted` is still active, wait for the next pg-backup CronJob cycle (03:00 UTC) — or trigger a manual one (Step 5).

- [ ] **Step 5: Optional — trigger an immediate pg-backup run**

```bash
kubectl -n lolday create job --from=cronjob/pg-backup pg-backup-manual-2026-05-16
kubectl -n lolday wait --for=condition=complete job/pg-backup-manual-2026-05-16 --timeout=5m
kubectl -n lolday logs job/pg-backup-manual-2026-05-16 --tail=30
```

Expected: a `pg_dumpall` line followed by upload to MinIO, then `mark backup as completed` and the Job goes `Complete`. The 18h-stuck `pg-backup-29647860` pod will continue to idle — clean it up explicitly:

```bash
kubectl -n lolday delete job pg-backup-29647860
```

- [ ] **Step 6: Confirm Captain Hook spam stops**

Use the `mcp__plugin_discord_discord__fetch_messages` tool on channel `1495778266907279410` with `limit: 5`. Expected: the last `@here` message timestamp is now older than 5 minutes (the rep_interval). If a new message arrives within 5 min, return to Step 3 and identify the new target failure.

---

## Task 12: Open PR, wait for CI, squash-merge

- [ ] **Step 1: Push branch**

```bash
git push -u origin fix-monitoring-np-alerts
```

- [ ] **Step 2: Open PR with full context**

```bash
gh pr create --title "fix(charts): close monitoring NP gaps + pg-backup BACKUP_ON_START (0.24.0 → 0.24.1)" \
  --body "$(cat <<'EOF'
## Summary

Closes the 16 currently-firing Prometheus / Alertmanager alerts caused by
the 2026-05-15 chart 0.24.0 NP hardening (PR #179, commit a05f3e2) plus
the pg-backup CronJob hang.

  - `monitoring-default-egress` was missing same-ns egress (Prom → AM,
    deadmans-switch → AM) and kubelet host-network egress (TCP 10250).
    The chart comment assumed `kube-system namespaceSelector` would cover
    kubelet, but kubelet has no pod label so the selector cannot match it.
  - `monitoring-internal-ingress` did not allow lolday ingress; the chart
    comment claimed backend → Prom was "monitoring-ns egress, not ingress",
    but that traffic is both. Added a narrow `prometheus-from-lolday-backend`
    NP scoped to backend pod + port 9090 only.
  - `harbor-internal-ingress-allow` only allows `app=harbor` ingress;
    Prometheus could not scrape harbor metrics. Added
    `harbor-metrics-from-monitoring-only` mirroring the existing
    `backend-metrics-from-monitoring-only` pattern.
  - `postgres-exporter` had no monitoring-ingress allow at all. Added
    `postgres-exporter-metrics-from-monitoring-only`.
  - `pg-backup` CronJob's container (`prodrigestivill/postgres-backup-local`)
    only runs at startup when `BACKUP_ON_START=TRUE`; without it the
    container idles forever and `KubeJobNotCompleted` fires. Added the env.
  - Docs: `Spidey Heartbeat` channel had been silent for 6 days because
    `deadmans-switch` is the SRE dead-man-switch pattern (failure-only),
    while `docs/operations.md` described it as a positive-heartbeat
    channel. Reconciled the docs to match implementation; filed positive-
    heartbeat as `architecture.md` §10 tech debt.
  - 6 images with CRITICAL CVE (Trivy) tracked separately as
    `architecture.md` §10 tech debt.

Live cluster verified before merge: `kubectl get networkpolicy` shows the
new resources, scrape targets recover, and Alertmanager active-alerts
drops from 16 → 2 (Watchdog + TrivyCriticalCVE).

Spec/plan: [`docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md`](docs/superpowers/plans/2026-05-16-monitoring-np-and-alerts-recovery.md)

## Test plan

- [ ] `helm lint charts/lolday` clean
- [ ] `helm unittest charts/lolday` — all NP suites pass
- [ ] `pre-commit run --all-files` clean
- [ ] Manual smoke: `kubectl -n monitoring get networkpolicy` shows new NPs
- [ ] Manual smoke: kubelet scrape target back to `up`
- [ ] Manual smoke: Captain Hook last `@here` older than 5 min after deploy
- [ ] Manual smoke: `pg-backup-manual-2026-05-16` completes within 5 min
EOF
)"
```

Capture the PR URL.

- [ ] **Step 3: Watch CI**

```bash
PR_URL=$(gh pr view --json url -q .url)
gh pr checks --watch
```

Expected: all required checks pass. If a check fails, fix the root cause in this branch — do NOT `--no-verify` or `gh pr merge --admin` unless GHA billing block is observed (per `reference_gha_billing_can_block_ci.md`).

- [ ] **Step 4: Squash-merge**

```bash
gh pr merge --squash --delete-branch
```

Authorized by the user up-front. Use `--admin` only if a billing block is observed.

- [ ] **Step 5: Verify merge on main**

```bash
git fetch origin main
git -C "$(git rev-parse --show-toplevel)" log --oneline origin/main -3
```

Expected: the new squash commit appears as `origin/main` HEAD.

- [ ] **Step 6: Exit worktree (keep on disk for the user to inspect)**

Use `ExitWorktree` with `action: keep` per the user's "Inline" execution mode.

---

## Out of scope (explicit non-goals)

- Trivy CRITICAL CVE image bumps (6 images × per-image research — filed as §10 tech debt).
- Positive-heartbeat plumbing for Spidey Heartbeat (operator needs to provision the webhook first — filed as §10 tech debt).
- kube-prometheus-stack chart upgrade or new rules.
- Branch protection / CI restructuring.

## Self-review checklist (apply once before committing the plan file)

- [x] Every step has runnable shell or exact YAML — no "TBD" / "implement later".
- [x] Same-name identifiers (NetworkPolicy names, env vars, secret keys) are consistent across tasks.
- [x] Each gap mentioned in the goal has a matching task: KubeletDown → Task 1, LoldayCoreServiceDown harbor → Task 3, postgres-exporter → Task 4, GpuSignalFailSafeStuck → Task 2, deadmans-switch spam → Task 1, KubeJobNotCompleted → Task 5, docs drift → Task 7, Trivy CVE → Task 8 tech debt.
- [x] Verification commands cite the exact output to look for.
- [x] No assumption that the live deploy MUST happen — the executing-plans agent can pause at Task 11 if the operator asks to defer the upgrade.
