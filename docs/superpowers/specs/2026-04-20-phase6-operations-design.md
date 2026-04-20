# Phase 6: Operations — Design Specification

## Overview

Phase 6 makes lolday reachable from the outside world and observable on the inside. Two deliverables, one phase:

1. **External access** — lab members in any network open `https://lolday.connlabai.com`, clear Cloudflare Access Zero Trust (Google SSO restricted to `@mail.ntust.edu.tw`), then the existing lolday login page, then the app. server30 opens no inbound port to the internet; Cloudflare Tunnel (cloudflared) dials out.
2. **In-cluster monitoring** — Prometheus + Alertmanager + Grafana + Loki + Promtail watch the cluster, backend, Traefik, Harbor, PostgreSQL, and the GPUs. Admin reaches Grafana via `kubectl port-forward`; no UI is exposed outside.

**Goal:** After Phase 6, a lab member at home opens `https://lolday.connlabai.com`, signs in with their NTUST Google account, logs into lolday, runs the full Phase 4 E2E flow (register detector → build → upload dataset → submit job → download predictions → promote model). On the same cluster, the admin port-forwards Grafana and sees live metrics from all platform components, GPUs included.

**Constraints:**
- SSH on server30 (port 9453) must keep working at every step. Cloudflare Tunnel adds nothing to the host firewall; cloudflared only dials outbound.
- User-land only. One bootstrap `sudo mkdir` for `/mnt/ssd500g/lolday-monitoring` (the admin has already done this during brainstorming).
- Backup, notifications, Volcano, NFS CSI, and Trivy Operator are deferred to Phase 7+ (see §11 Out of Scope).
- No China-origin components (Taiwan lab preference).
- Open-source first; the whole phase adds only a few lines of backend code (one Prometheus instrumentation line).
- Phase 4 E2E (curl) and Phase 5 E2E (Playwright, 5 specs) must keep passing after every sub-step.
- Admin has `sudo` during the one-time `mkdir`; all other work runs as `bolin8017` with `kubectl` access.

---

## Scope

Phase 6 covers main spec §8 (Cloudflare Tunnel + Access), §10 (monitoring & observability), and the external-access part of §9 (security). §11 (notifications) and §12 (backup) are explicitly deferred.

### In scope

1. **Cloudflare Tunnel** — `cloudflared` Deployment with 2 replicas for HA, connecting server30 to Cloudflare's edge. The Tunnel routes `lolday.connlabai.com` to the in-cluster Traefik service.
2. **Cloudflare Access Zero Trust** — one self-hosted application (`lolday.connlabai.com`), one policy (allow emails ending in `@mail.ntust.edu.tw`), Google as identity provider. 24-hour session.
3. **DNS** — `lolday.connlabai.com` CNAME to `<tunnel-id>.cfargotunnel.com`, managed in Cloudflare DNS.
4. **Monitoring stack** (new namespace `monitoring`):
   - `kube-prometheus-stack` Helm chart — Prometheus, Alertmanager, Grafana, kube-state-metrics, node-exporter, Prometheus Operator.
   - `loki` Helm chart — single-binary mode, filesystem chunks.
   - `promtail` Helm chart — DaemonSet scraping `/var/log/containers/*.log`.
   - PostgreSQL exporter as a standalone Deployment.
5. **Storage** — new StorageClass `monitoring-local` backed by `/mnt/ssd500g/lolday-monitoring` (NVMe, 348 GB free). All monitoring PVs bind here to avoid filling the root filesystem (`/` has only ~42 GB free).
6. **Backend instrumentation** — add `prometheus-fastapi-instrumentator` so Prometheus scrapes `/metrics` on backend. Image bumps `phase5` → `phase6`.
7. **ServiceMonitors** — backend, Traefik, Harbor, PostgreSQL exporter. DCGM exporter (already in `gpu-operator` ns) scraped via its existing Service.
8. **Alert rules** — four baseline rules: node disk ≥ 85 %, GPU temperature ≥ 85 °C, pod CrashLoopBackOff ≥ 5 min, core service down ≥ 2 min. Alertmanager receivers stay empty; alerts appear in the UI for now.
9. **Grafana dashboards** — auto-provisioned from ConfigMap: Kubernetes cluster / nodes, NVIDIA DCGM (id `12239`), Traefik (id `17346`), FastAPI, Loki logs browser, PostgreSQL.
10. **NetworkPolicy** — restrict `cloudflared` pod egress to Traefik + DNS + Cloudflare edge only.
11. **Phase 6 E2E checklist** — `docs/phase6-e2e-checklist.md`, parallel in style to Phase 3/4.

### Out of scope (deferred to Phase 7+)

- **Backup** — pg_dump, etcd snapshot, MLflow artifact sync to Cloudflare R2. Deferred by user decision during brainstorm (not urgent for internal lab use).
- **Notifications** — Resend email for job complete / build complete / Trivy blocked. Alertmanager receivers for admin alerts. Alertmanager is deployed; receivers stay empty and can be added later without redeploying the chart.
- **External admin UIs** — Grafana, MLflow, Alertmanager, Harbor exposed as separate Cloudflare-protected subdomains. Admin reaches them via `kubectl port-forward` for now.
- **Volcano scheduler** — fair-share GPU queue. Current per-user concurrency limit (Phase 4) is enough for lab scale.
- **NFS CSI driver** — replace hostPath `samples` PV. Current hostPath is adequate and no shared NFS server exists.
- **Trivy Operator** — cluster-wide image scanning. Harbor's built-in Trivy already scans on push.
- **MFA in Cloudflare Access** — NTUST Google Workspace already enforces its own MFA policy; forcing a second factor at the Cloudflare layer would duplicate it.
- **Individual email allowlist** — using domain-based policy (`ends_with @mail.ntust.edu.tw`) removes the need to edit a list whenever a member joins or leaves.
- **Discord webhook, audit log UI, gVisor, etcd encryption at rest, in-app notification feed** — kept as v2+ per main spec §14.

---

## Architecture

```
External user (laptop / phone, any network)
  │  HTTPS
  ▼
┌─────────────────────────────────────────────────────────────┐
│ Cloudflare edge                                             │
│  • DNS: lolday.connlabai.com → CNAME → <uuid>.cfargotunnel  │
│  • DDoS protection (free tier)                              │
│  • Access Zero Trust (Google SSO; @mail.ntust.edu.tw only)  │
│    ↓ issues JWT header cf-access-jwt-assertion on success   │
└──────────────────┬──────────────────────────────────────────┘
                   │  outbound-only encrypted tunnel
                   │  (cloudflared dials out to Cloudflare)
                   ▼
┌─────────────────────────────────────────────────────────────┐
│ server30 / K3s                                              │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ namespace: lolday                                       │ │
│ │                                                         │ │
│ │  cloudflared Deployment (replicas: 2)                   │ │
│ │    image: cloudflare/cloudflared                        │ │
│ │    NetworkPolicy: egress → kube-system/traefik, DNS,    │ │
│ │                   *.cloudflare.com:443 only             │ │
│ │    │ HTTP to Service                                    │ │
│ │    ▼                                                    │ │
│ │  traefik (kube-system, existing)                        │ │
│ │    IngressRoute "lolday":                               │ │
│ │      Host: lolday.connlabai.com (host rule updated)     │ │
│ │        /api/v1/* → backend:8000                         │ │
│ │        /*        → frontend:80                          │ │
│ │                                                         │ │
│ │  backend (existing + /metrics endpoint)                 │ │
│ │  frontend, mlflow, postgres, redis, harbor (existing)   │ │
│ │  postgres-exporter Deployment (new)                     │ │
│ │                                                         │ │
│ │  ServiceMonitors:                                       │ │
│ │    backend, traefik, harbor, postgres-exporter          │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ namespace: monitoring (new)                             │ │
│ │                                                         │ │
│ │  prometheus-operator                                    │ │
│ │  prometheus  (20 Gi PVC on monitoring-local StorageClass│ │
│ │               → /mnt/ssd500g/lolday-monitoring)         │ │
│ │  alertmanager (2 Gi PVC; receivers empty)               │ │
│ │  grafana (5 Gi PVC; provisioned dashboards + datasources│ │
│ │           + admin password from Secret)                 │ │
│ │  kube-state-metrics                                     │ │
│ │  node-exporter (DaemonSet)                              │ │
│ │  loki (30 Gi PVC; single-binary mode, filesystem chunks)│ │
│ │  promtail (DaemonSet; scrapes /var/log/containers/*)    │ │
│ │                                                         │ │
│ │  Auto-discovered scrape targets:                        │ │
│ │    kube-apiserver, kubelet, kube-state-metrics,         │ │
│ │    node-exporter                                        │ │
│ │  Manual ServiceMonitors reach into lolday and           │ │
│ │  gpu-operator namespaces                                │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ namespace: gpu-operator (existing; no change)           │ │
│ │  dcgm-exporter (DaemonSet; Service :9400/metrics)       │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ Disks                                                       │
│   /                   → K3s local-path (phase 1–5 data)     │
│   /mnt/ssd500g/...   → monitoring-local PVs only            │
│   /data/samples      → malware samples (existing)           │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Cloudflare Tunnel (cloudflared)

**Purpose:** Outbound-only dial-out from the cluster to Cloudflare's edge. Replaces the need for an inbound public IP and NAT rules.

**Chart template:** `charts/lolday/templates/cloudflared.yaml` already exists from Phase 1 scaffolding (gated by `cloudflare.enabled`). Phase 6 flips `cloudflare.enabled: true` and supplies the token.

**Deployment:**
- Image: `cloudflare/cloudflared:2026.3.0` (pin an explicit tag; auto-`latest` is a supply-chain risk).
- Replicas: 2 (HA — losing one does not interrupt users).
- Runs `tunnel --no-autoupdate run --token $TUNNEL_TOKEN`; the token identifies the tunnel, configures public hostnames, and carries routing rules (managed in Cloudflare dashboard, not in YAML).
- Resources: request 50 m CPU / 64 Mi; limit 200 m / 128 Mi.
- Probes: `httpGet /ready` on port 2000.
- SecurityContext: `runAsNonRoot`, `readOnlyRootFilesystem`, drop `ALL` capabilities, `allowPrivilegeEscalation: false`.

**NetworkPolicy (`templates/netpol-cloudflared.yaml`):**
- Egress allowed only to:
  - `kube-system/traefik` Service (port 80).
  - `kube-dns` (port 53 UDP/TCP).
  - `*.cloudflare.com:443` (TCP), which is the edge control plane.
- All other egress denied.
- Even if cloudflared is compromised, it cannot reach backend / PostgreSQL / MLflow directly; all traffic still flows through Traefik's route policy.

**Cloudflare dashboard configuration (manual, one-time):**
- Zero Trust → Access → Tunnels → Create tunnel → Name `lolday-server30`.
- Copy the tunnel token; paste into `~/.lolday-secrets.env` as `TUNNEL_TOKEN`.
- Public Hostnames:
  - Subdomain: `lolday`
  - Domain: `connlabai.com`
  - Type: HTTP
  - URL: `traefik.kube-system.svc.cluster.local:80`
  - Additional: `Host` header override to `lolday.connlabai.com` (so the existing Traefik IngressRoute host-rule matches).

### 2. Cloudflare Access Zero Trust

**Purpose:** Gate the Tunnel entry at Cloudflare's edge so only authenticated NTUST members reach the cluster. Two layers of auth (Access + lolday login) = defense in depth.

**Configuration (Cloudflare dashboard, one-time):**
- Zero Trust → Settings → Authentication → Login methods → add Google as an IdP.
- Access → Applications → Add a self-hosted app.
  - Application name: `lolday`
  - Application domain: `lolday.connlabai.com`
  - Session duration: 24 hours.
- Add a policy:
  - Action: `Allow`
  - Rule: `Emails ending in` → `@mail.ntust.edu.tw`
  - Include: Google IdP
- Leave MFA off (NTUST Google accounts enforce their own MFA where required).
- Save. The policy is immediately active; users without an NTUST email see the Access Denied page instead of the lolday login.

**Integration detail:** Cloudflare Access injects a JWT into every request (`cf-access-jwt-assertion` header). The backend currently ignores it, which is fine — the lolday platform continues to authenticate users via FastAPI Users cookie/bearer. If a Phase 7+ needs SSO bypass (skip lolday login for Access-verified users), the backend can verify this JWT and mint a session.

### 3. Traefik IngressRoute

**Change required:** The existing `IngressRoute lolday` (from Phase 5) matches on host `lolday.islab.local`. Phase 6 updates `values.yaml` `frontend.host` from `lolday.islab.local` to `lolday.connlabai.com`. The IngressRoute template re-renders accordingly.

**Intentional removal of LAN bypass:** `lolday.islab.local` (Phase 5's LAN-only hostname) stops being routable after the change. This is deliberate — we want every request, LAN or not, to pass through Cloudflare Access, so no one can sidestep Zero Trust by hitting the cluster directly. The Playwright host-resolver rule (Phase 5's workaround that mapped `lolday.islab.local` → Traefik LoadBalancer) is removed in the same commit and the `baseURL` in `playwright.config.ts` points at `https://lolday.connlabai.com`. Admin maintenance that must skip Cloudflare (e.g., during a Cloudflare outage) uses `kubectl port-forward svc/traefik -n kube-system 8080:80` and `curl -H 'Host: lolday.connlabai.com' http://localhost:8080/...`.

### 4. kube-prometheus-stack

**Purpose:** Metrics collection, storage, alerting, dashboards.

**Helm chart:** `prometheus-community/kube-prometheus-stack`, pinned to `~60.0.0`.

**Sub-values (`values.yaml` keys under `kube-prometheus-stack:`):**

```yaml
namespaceOverride: monitoring     # chart lives here, ns created separately via our template
fullnameOverride: kps             # short prefix keeps names readable

prometheus:
  prometheusSpec:
    retention: 15d
    retentionSize: 18GiB          # ~10 % under PVC size to leave head-room
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: monitoring-local
          resources: { requests: { storage: 20Gi } }
    serviceMonitorSelectorNilUsesHelmValues: false   # scrape ALL ServiceMonitors regardless of release label
    podMonitorSelectorNilUsesHelmValues: false
    resources:
      requests: { cpu: 500m, memory: 2Gi }
      limits:   { cpu: 2,    memory: 8Gi }

alertmanager:
  alertmanagerSpec:
    storage:
      volumeClaimTemplate:
        spec:
          storageClassName: monitoring-local
          resources: { requests: { storage: 2Gi } }
  config:
    route:
      receiver: 'null'            # empty on purpose; Phase 7 wires Resend
      group_wait: 10s
      group_interval: 5m
      repeat_interval: 12h
    receivers:
      - name: 'null'

grafana:
  persistence:
    enabled: true
    storageClassName: monitoring-local
    size: 5Gi
  admin:
    existingSecret: grafana-admin
    userKey: admin-user
    passwordKey: admin-password
  defaultDashboardsEnabled: true
  sidecar:
    dashboards: { enabled: true, searchNamespace: ALL }
    datasources: { enabled: true }
  resources:
    requests: { cpu: 100m, memory: 256Mi }
    limits:   { cpu: 500m, memory: 1Gi }

kubeStateMetrics: { enabled: true }
nodeExporter:     { enabled: true }
```

**Secret `grafana-admin`:** created by `scripts/deploy.sh` from `GRAFANA_ADMIN_PASSWORD` in `~/.lolday-secrets.env`. Two keys: `admin-user=admin`, `admin-password=<random>`.

### 5. Loki + Promtail

**Charts:** `grafana/loki` ~6.0.0 (single-binary mode), `grafana/promtail` ~6.0.0.

**Loki values:**

```yaml
loki:
  commonConfig:
    replication_factor: 1         # single-binary, no HA needed for lab scale
  storage:
    type: filesystem
  auth_enabled: false
  limits_config:
    retention_period: 168h        # 7 days
singleBinary:
  replicas: 1
  persistence:
    enabled: true
    storageClass: monitoring-local
    size: 30Gi
  resources:
    requests: { cpu: 200m, memory: 512Mi }
    limits:   { cpu: 1,    memory: 2Gi }
read:    { replicas: 0 }
write:   { replicas: 0 }
backend: { replicas: 0 }
```

**Promtail values:**

```yaml
config:
  clients:
    - url: http://loki.monitoring.svc:3100/loki/api/v1/push
  snippets:
    scrapeConfigs: |
      - job_name: kubernetes-pods
        kubernetes_sd_configs:
          - role: pod
        pipeline_stages:
          - cri: {}
        relabel_configs:
          - source_labels: [__meta_kubernetes_namespace]
            target_label: namespace
          - source_labels: [__meta_kubernetes_pod_name]
            target_label: pod
          - source_labels: [__meta_kubernetes_pod_container_name]
            target_label: container
          - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_component]
            target_label: component
resources:
  requests: { cpu: 50m,  memory: 64Mi }
  limits:   { cpu: 200m, memory: 256Mi }
```

### 6. PostgreSQL exporter

**Deployment `postgres-exporter`** (`templates/monitoring/postgres-exporter.yaml`):
- Image: `quay.io/prometheuscommunity/postgres-exporter:v0.17.0`.
- Env `DATA_SOURCE_NAME: postgresql://postgres_exporter:<pw>@postgresql.lolday.svc:5432/lolday?sslmode=disable`.
- Service on port 9187.
- Resources: 20 m / 64 Mi / 100 m / 128 Mi.

**DB user:** `scripts/deploy.sh` runs on first deploy:
```sql
CREATE USER postgres_exporter WITH PASSWORD '<random>';
GRANT pg_monitor TO postgres_exporter;
```
Password goes to `~/.lolday-secrets.env` as `PG_EXPORTER_PASSWORD`.

**ServiceMonitor** selects `app.kubernetes.io/name=postgres-exporter`, path `/metrics`, interval 30s.

### 7. ServiceMonitors (manual list)

| target | namespace | path | port |
|---|---|---|---|
| backend | lolday | /metrics | 8000 |
| traefik | kube-system | /metrics | traefik (the Prometheus endpoint is enabled by default in K3s Traefik; verify and enable if not) |
| harbor | lolday | /metrics | selected Harbor services expose `/metrics` (core, jobservice, registry) |
| postgres-exporter | lolday | /metrics | 9187 |

DCGM exporter is not a custom ServiceMonitor: the NVIDIA GPU Operator already provides `ServiceMonitor/nvidia-dcgm-exporter` in the `gpu-operator` namespace, and kube-prometheus-stack (with our `serviceMonitorSelectorNilUsesHelmValues: false`) picks it up automatically.

### 8. Alert rules

`templates/monitoring/alertmanager-rules.yaml` (a `PrometheusRule` CR):

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: lolday-baseline
  namespace: monitoring
spec:
  groups:
    - name: lolday-baseline.rules
      rules:
        - alert: NodeDiskAlmostFull
          expr: (node_filesystem_size_bytes{mountpoint=~"/|/mnt/ssd500g"} - node_filesystem_avail_bytes) / node_filesystem_size_bytes > 0.85
          for: 10m
          labels: { severity: critical }
          annotations:
            summary: "Node disk > 85% on {{ $labels.instance }} ({{ $labels.mountpoint }})"

        - alert: GPUTemperatureHigh
          expr: DCGM_FI_DEV_GPU_TEMP > 85
          for: 5m
          labels: { severity: critical }
          annotations:
            summary: "GPU {{ $labels.gpu }} on {{ $labels.Hostname }} is above 85°C"

        - alert: PodCrashLoopBackOff
          expr: kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1
          for: 5m
          labels: { severity: warning }
          annotations:
            summary: "Pod {{ $labels.namespace }}/{{ $labels.pod }} is CrashLoopBackOff"

        - alert: LoldayCoreServiceDown
          expr: up{job=~"backend|postgres-exporter|harbor"} == 0
          for: 2m
          labels: { severity: critical }
          annotations:
            summary: "Core service {{ $labels.job }} is down"
```

With empty Alertmanager receivers, these fire into the Alertmanager UI (visible via port-forward) but send nowhere. Wiring Resend / Discord is a one-YAML-patch job in Phase 7.

### 9. Grafana dashboards (auto-provisioned)

ConfigMap `grafana-dashboards` (`templates/monitoring/grafana-dashboards.yaml`) holds the JSON. Labels `grafana_dashboard: "1"` trigger the Grafana sidecar to load them.

Sources:
- Kubernetes + Nodes + Cluster: bundled with kube-prometheus-stack.
- NVIDIA DCGM Exporter: grafana.com id `12239`.
- Traefik: grafana.com id `17346`.
- PostgreSQL (pgExporter): grafana.com id `9628`.
- Loki logs: bundled with Loki.

Admin fetches each JSON once during plan execution and checks them in alongside the ConfigMap.

### 10. Backend `/metrics`

`backend/requirements.txt` gains one line:
```
prometheus-fastapi-instrumentator==7.*
```

`backend/app/main.py` gains three lines after the app is constructed:
```python
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False,
)
```

`include_in_schema=False` keeps the endpoint out of the OpenAPI spec, so Phase 5's generated TypeScript client does not leak metrics into UI types. Phase 5 Playwright E2E keeps passing; a spot-check `curl http://backend.lolday.svc:8000/metrics` must return Prometheus text.

---

## Data Flow

### External request (happy path)

```
user browser
  → DNS: lolday.connlabai.com resolves to Cloudflare edge IP
  → TLS to Cloudflare edge
  → Cloudflare Access: checks for valid Access JWT
      no JWT → 302 to https://<team>.cloudflareaccess.com/cdn-cgi/access/...
      user completes Google SSO at NTUST
      Cloudflare issues JWT cookie (session 24h)
  → request re-issued with JWT
  → Cloudflare Tunnel forwards to cluster cloudflared
  → cloudflared → Traefik (Host: lolday.connlabai.com)
  → Traefik IngressRoute:
      /api/v1/*  → backend
      /*         → frontend
  → FastAPI Users cookie auth (lolday login)
  → requested resource
```

### Metrics flow

```
node-exporter   (DaemonSet per node, :9100/metrics)
kubelet         (auto-discovered)
kube-state-metrics (cluster state)
dcgm-exporter   (gpu-operator/:9400/metrics)
backend         (/metrics via instrumentator)
traefik         (/metrics from Traefik service)
harbor core     (/metrics)
postgres-exporter (/metrics)
    │ Prometheus scrape (15–60 s interval, per ServiceMonitor)
    ▼
prometheus (TSDB on /mnt/ssd500g/lolday-monitoring/prometheus)
    │
    ├─ Grafana query (PromQL)
    └─ PrometheusRule evaluate
         │ alert fires
         ▼
       alertmanager (UI only; receivers empty in Phase 6)
```

### Log flow

```
container stdout/stderr → kubelet → /var/log/containers/*.log
    │
    ▼
promtail (DaemonSet, reads files, adds pod/namespace/container labels)
    │ HTTP push
    ▼
loki (chunks on /mnt/ssd500g/lolday-monitoring/loki)
    │
    ▼
grafana (Explore → Loki datasource → LogQL)
```

---

## Security

### Attack surfaces and how they're covered

| Attack | Mitigation layer |
|---|---|
| Port scan on server30 public IP | No public IP is opened by Phase 6; Tunnel dials out. SSH on 9453 stays as-is (pre-existing admin responsibility). |
| DDoS | Cloudflare edge absorbs volumetric attacks before they reach the tunnel. |
| Brute-force on lolday login | Attacker cannot reach the login page — Cloudflare Access requires `@mail.ntust.edu.tw` Google SSO first. |
| Random Google account (not NTUST) tries to pass Access | Cloudflare Access policy denies — user sees Access Denied, not lolday login. |
| NTUST account with phished Google password | Attacker still needs lolday platform credentials (FastAPI Users). Two layers must both fail. |
| cloudflared container RCE | NetworkPolicy restricts egress to Traefik, DNS, Cloudflare edge. Cannot pivot to backend / DB directly. SecurityContext drops all capabilities. |
| Compromise of backend with /metrics exposed | `/metrics` has no privileged data (pure Prometheus counters); `include_in_schema=False` so not in OpenAPI. |
| Grafana public exposure | Grafana is not in the Cloudflare Tunnel routes; unreachable from outside. Admin ports-forwards it. |

### Residual risks (accepted for Phase 6)

- **Host-level compromise on server30** — SSH hardening, fail2ban, host firewall are the admin's responsibility outside this phase.
- **Container escape** — standard K8s SecurityContext applied; gVisor / Kata is v2+.
- **Internal malicious user** — RBAC (admin/developer/user) limits blast radius but admins see everything. No platform-level audit log (Loki records HTTP traffic, not semantic events).
- **Cloudflare outage** — external access breaks; internal work via SSH + `kubectl port-forward` continues. Spec documents this so no panic during an outage.
- **LAN bypass of Cloudflare Access** — anyone with SSH to server30 or on the same K8s LAN segment can reach Traefik directly (`--resolve lolday.connlabai.com:80:<traefik-lb-ip>`), skipping the Access / Google-SSO layer. They still hit the lolday platform login and need valid platform credentials, so it reduces from "two factors" to "one factor" — not zero. Closing this gap requires backend JWT verification (Cloudflare Access `cf-access-jwt-assertion` header) and is deferred to Phase 7+.

### Secrets

New entries in `~/.lolday-secrets.env`:
```bash
export TUNNEL_TOKEN="eyJhIjoi..."                          # Cloudflare Tunnel token
export GRAFANA_ADMIN_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
export PG_EXPORTER_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
```

`scripts/deploy.sh` passes them to `helm upgrade --install` as `--set` values; they flow into K8s Secrets (`cloudflared-tunnel-token`, `grafana-admin`, `postgres-exporter-db-credentials`) and never land in YAML checked into git.

---

## Deployment

Three sub-phases. Each sub-phase has an explicit exit criterion; failing one aborts the rollout.

### Sub-phase 6-1: Monitoring stack

No external-access change. Pure additive.

1. `sudo mkdir -p /mnt/ssd500g/lolday-monitoring && sudo chown $USER:$USER $_` (done).
2. Bump `charts/lolday/Chart.yaml` deps: add `kube-prometheus-stack`, `loki`, `promtail`.
3. `helm dependency update charts/lolday`.
4. Add `templates/monitoring/` files (namespace, storage-class, servicemonitors, alert-rules, dashboards, postgres-exporter).
5. Add monitoring blocks to `values.yaml`.
6. Add `prometheus-fastapi-instrumentator` to backend; bump image tag to `phase6`; push.
7. Run `scripts/deploy.sh`.
8. Verify (see Testing §): all pods `Running`, Grafana shows DCGM + K8s dashboards, Loki shows backend logs.

### Sub-phase 6-2: Cloudflare Access policy (dashboard-only, tunnel not yet live)

Pure Cloudflare-side configuration. No cluster change.

1. Create Tunnel in Zero Trust → Tunnels → `lolday-server30`. Copy token. Do **not** add public hostname yet.
2. Add Google IdP (first time: follow Cloudflare's OAuth consent flow).
3. Create Access Application `lolday`, domain `lolday.connlabai.com`, session 24h.
4. Create policy: Allow, Emails-ending-in `@mail.ntust.edu.tw`, Include Google IdP.
5. Save `TUNNEL_TOKEN` to `~/.lolday-secrets.env`.
6. Verify: Cloudflare dashboard shows the application with status "Policy pending — no tunnel". Access policy is live even without a tunnel; any request that arrives at that hostname hits Access first.

### Sub-phase 6-3: Enable Tunnel + DNS

1. In Cloudflare Zero Trust → Tunnels → `lolday-server30`, add Public Hostname: `lolday.connlabai.com` → HTTP → `traefik.kube-system.svc.cluster.local:80` with `Host` header override `lolday.connlabai.com`.
2. Cloudflare auto-creates the CNAME `lolday.connlabai.com → <uuid>.cfargotunnel.com`.
3. Bump `values.yaml`: `cloudflare.enabled: true`, `frontend.host: lolday.connlabai.com`.
4. `scripts/deploy.sh` — deploys cloudflared Deployment and NetworkPolicy; re-renders the IngressRoute host rule.
5. Wait for `kubectl -n lolday get pod -l app.kubernetes.io/component=cloudflared` to show 2/2 Ready and the cloudflared logs print `Registered tunnel connection`.
6. Verify (see Testing): external anonymous request → Access Denied; NTUST Google SSO → lolday login → app.

**Exposure window:** none. Access policy is live in 6-2, so the first instant DNS resolves and a request arrives, Access blocks it unless the user has a valid NTUST session.

**Rollback:** `helm rollback lolday <N>` reverts everything including the IngressRoute host change. Cloudflare policy in 6-2 can stay (harmless); tunnel can be disabled in the Cloudflare dashboard with a toggle.

---

## Testing

### Per-sub-phase exit criteria

**6-1 exit:**
- All pods in `monitoring` ns `Running`, no `CrashLoopBackOff`.
- Grafana reachable via port-forward, login works.
- Dashboards "Kubernetes / Compute Resources / Cluster" and "NVIDIA DCGM Exporter" populated within 2 minutes of deploy.
- `{namespace="lolday",app="backend"}` LogQL returns backend log lines.
- `kubectl -n monitoring exec prometheus-kps-prometheus-0 -- wget -qO- localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.health=="up") | .labels.job'` returns ≥ 10 distinct jobs.
- Alertmanager UI at port-forward 9093 shows the 4 baseline rules as `inactive`.

**6-2 exit:**
- Cloudflare dashboard: `lolday` application exists with policy "Allow @mail.ntust.edu.tw" active.
- `cloudflared access login https://lolday.connlabai.com` (admin's local laptop) returns a token after Google SSO.

**6-3 exit:**
- `kubectl -n lolday get pod -l app.kubernetes.io/component=cloudflared` shows 2/2 Ready.
- Cloudflared logs on both replicas include `Registered tunnel connection connIndex=0` and `connIndex=1`.
- Anonymous `curl -I https://lolday.connlabai.com` redirects (302) to `*.cloudflareaccess.com`.
- Non-NTUST Google account signing in → Access denied page.
- NTUST Google account → lolday login page renders; lolday credentials log the user in; Phase 4 E2E flow completes.

### Regression gates (run after each sub-phase)

- **Phase 4 curl E2E** (`docs/phase4-e2e-checklist.md`): register detector → build → dataset → train → evaluate → predict → download. Every step returns 2xx. The test runs with `kubectl port-forward svc/backend 8000:8000` (in-cluster), so Cloudflare Access is not in the path.
- **Phase 5 Playwright E2E** (5 specs): all pass. `playwright.config.ts` keeps its Chromium `--host-resolver-rules` flag, but the rule now maps `lolday.connlabai.com` → Traefik LoadBalancer IP (previously `lolday.islab.local`). The tests bypass Cloudflare entirely and hit Traefik directly on the LAN, which is safe because (a) tests run from the admin's laptop SSHed into server30, and (b) Traefik does not see Access headers either way — only the hostname. After 6-3, this is the only supported non-Cloudflare entry path and is spelled out in `docs/phase6-e2e-checklist.md`.

### Chaos test (performed manually after 6-3)

- `kubectl -n lolday delete pod -l app.kubernetes.io/component=cloudflared` on one replica → external access uninterrupted.
- Delete both → external access drops; SSH + port-forward still work. Replicas recover on restart.
- Fill `/mnt/ssd500g/lolday-monitoring` artificially (`fallocate`) → `NodeDiskAlmostFull` alert fires; lolday itself unaffected.

### Security verification (manual)

```bash
# A) Anonymous request blocked by Access
curl -sI https://lolday.connlabai.com | grep -i location
# expect: location: https://<team>.cloudflareaccess.com/...

# B) Direct POST to /api/v1/auth/login also blocked
curl -siX POST https://lolday.connlabai.com/api/v1/auth/login -d 'x=1' | head -5
# expect: 302 to cloudflareaccess, not 422/401 from FastAPI

# C) With valid Access token, API reachable
cloudflared access login https://lolday.connlabai.com
TOKEN=$(cloudflared access token -app=https://lolday.connlabai.com)
curl -sH "cf-access-token: $TOKEN" https://lolday.connlabai.com/api/v1/health
# expect: {"status":"ok"} or equivalent
```

### Verification checklist

All of the above go into `docs/phase6-e2e-checklist.md`, structured like Phase 3 / Phase 4 checklists. It lives alongside the other phase checklists and is the single source of truth for "Phase 6 is done".

---

## File Structure

New and changed files, relative to repo root:

```
backend/
  app/main.py                          # +3 lines: Instrumentator().instrument().expose()
  requirements.txt                     # +1 line: prometheus-fastapi-instrumentator==7.*

charts/lolday/
  Chart.yaml                           # +3 deps: kube-prometheus-stack, loki, promtail
  Chart.lock                           # regenerated
  values.yaml                          # add monitoring: loki: promtail: cloudflare: blocks; update frontend.host
  templates/
    cloudflared.yaml                   # unchanged (already exists, toggled by cloudflare.enabled)
    cloudflared-secret.yaml            # unchanged
    netpol-cloudflared.yaml            # NEW: egress restriction for cloudflared
    ingress.yaml                       # host rule now ${values.frontend.host}, which is lolday.connlabai.com
    monitoring/
      namespace.yaml                   # NEW: monitoring ns
      storage-class.yaml               # NEW: monitoring-local
      servicemonitor-backend.yaml      # NEW
      servicemonitor-traefik.yaml      # NEW
      servicemonitor-harbor.yaml       # NEW
      servicemonitor-postgres.yaml     # NEW
      postgres-exporter.yaml           # NEW: Deployment + Service + Secret bootstrap
      alertmanager-rules.yaml          # NEW: PrometheusRule with 4 alerts
      grafana-dashboards.yaml          # NEW: ConfigMap(s) with provisioned JSON
  helpers/
    (no new helper images required)

scripts/
  deploy.sh                            # add --set cloudflare.enabled / tunnelToken / grafana admin / pg exporter pw
  phase6-pre-deploy-check.sh           # NEW: disk, secrets, tunnel, DNS, Phase 4/5 E2E pre-flight

frontend/
  playwright.config.ts                 # baseURL: lolday.connlabai.com; remove host-resolver-rules override
  (no SPA logic changes)

docs/
  phase6-e2e-checklist.md              # NEW: exit criteria + regression + chaos + security checklist
  superpowers/specs/
    2026-04-20-phase6-operations-design.md  # this file
```

Existing files unchanged: all Phase 2 / 3 / 4 backend routes, all Phase 5 frontend SPA components.

---

## Open Questions

- None blocking. The Tunnel token cannot be generated in advance of sub-phase 6-2, so the plan assumes the admin runs that manual Cloudflare dashboard step when sub-phase 6-1 is complete.
- Grafana dashboard JSON snapshots are captured once during plan execution; future Grafana upstream updates may require re-export.

---

## Future phases (after Phase 6)

- **Phase 7: Notifications** — wire Alertmanager to Resend email; add user-facing notifications (job complete / build complete / Trivy blocked) via the same Resend account.
- **Phase 8: Backup** — pg_dump, etcd snapshot, MLflow rsync to Cloudflare R2.
- **Phase 9+: admin UIs external, Volcano, NFS CSI, Trivy Operator, audit log** — as demand grows.
