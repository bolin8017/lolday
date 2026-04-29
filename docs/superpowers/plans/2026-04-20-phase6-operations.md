# Phase 6: Operations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship external access (`https://lolday.connlabai.com` gated by Cloudflare Tunnel + Zero Trust with NTUST Google SSO) plus in-cluster monitoring (kube-prometheus-stack + Loki + Promtail + DCGM). Server30 continues to open zero inbound ports to the public internet. Phase 4 curl E2E and Phase 5 Playwright E2E must still pass end-to-end at every step.

**Architecture:** Three sub-phases land incrementally. **6-1** adds a new `monitoring` namespace (Prometheus + Alertmanager + Grafana + Loki + Promtail + postgres-exporter) and instruments the backend with `/metrics`. **6-2** sets up the Cloudflare Access application + Google SSO policy in Cloudflare dashboard (no cluster change). **6-3** flips `cloudflare.enabled=true`, updates `frontend.host` to `lolday.connlabai.com`, adds the NetworkPolicy restricting `cloudflared` egress, and points the Cloudflare Tunnel Public Hostname at in-cluster Traefik. Between 6-2 and 6-3 there is no public-exposure window, because the Access policy is live before the DNS CNAME is created.

**Tech Stack:** Helm v3, `kube-prometheus-stack` ~60.0.0, `loki` ~6.0.0, `promtail` ~6.0.0, `prometheus-fastapi-instrumentator` ~7.0, `postgres-exporter` v0.17.0, Traefik 3.x (K3s built-in), `cloudflared` 2026.3.x, Grafana 11.x, Loki 3.x.

**Spec:** `docs/superpowers/specs/2026-04-20-phase6-operations-design.md`

**Server:** server30 (Ubuntu 24.04, K3s v1.34.6+k3s1, Phase 4+5 deployed — backend `phase5`, frontend `phase5`, MLflow, PostgreSQL, Harbor, Redis).

**Constraints:**

- `bolin8017` has no persistent `sudo` — one `sudo mkdir` for `/mnt/ssd500g/lolday-monitoring` has already been done during brainstorming.
- CLI tools in `~/.local/bin/`; do NOT system-install anything without explicit approval.
- SSH on port 9453 must never be disrupted. K3s must stay running after every step.
- No Cilium / no CNI change. Current Flannel + kube-router stays.
- Phase 4 curl E2E + Phase 5 Playwright E2E are regression gates — re-run after every sub-phase.
- Backend-side change is limited to adding one Instrumentator line + the `/metrics` endpoint. All existing APIs keep working unchanged.
- Backup, Resend notifications, Volcano, NFS CSI, Trivy Operator are OUT OF SCOPE.

---

## File Structure

```
backend/
├── app/
│   └── main.py                       # +3 lines: Instrumentator().instrument().expose()
├── pyproject.toml                     # +1 dep: prometheus-fastapi-instrumentator ~= 7.0
├── uv.lock                            # regenerated
└── tests/
    └── test_metrics.py                # NEW: /metrics endpoint contract test

charts/lolday/
├── Chart.yaml                         # +3 deps: kube-prometheus-stack, loki, promtail
├── Chart.lock                         # regenerated
├── .helmignore                        # adds charts/*.tgz (if not already)
├── values.yaml                        # + monitoring blocks; update frontend.host to lolday.connlabai.com
└── templates/
    ├── cloudflared.yaml               # unchanged (already templated, gated by cloudflare.enabled)
    ├── cloudflared-secret.yaml        # unchanged
    ├── netpol-cloudflared.yaml        # NEW: egress restriction for cloudflared pods
    ├── ingress.yaml                   # NO CHANGE — host pulls from values.frontend.host
    └── monitoring/
        ├── namespace.yaml             # NEW
        ├── storage-class.yaml         # NEW: monitoring-local → /mnt/ssd500g/lolday-monitoring
        ├── grafana-admin-secret.yaml  # NEW: Secret "grafana-admin" with admin-user / admin-password
        ├── servicemonitor-backend.yaml    # NEW
        ├── servicemonitor-traefik.yaml    # NEW
        ├── servicemonitor-postgres.yaml   # NEW  (Harbor uses its own chart-provided SM)
        ├── postgres-exporter.yaml         # NEW: Deployment + Service + Secret
        ├── postgres-exporter-initjob.yaml # NEW: run-once Job that creates postgres_exporter DB user
        ├── alertmanager-rules.yaml        # NEW: PrometheusRule with 4 alerts
        └── grafana-dashboards.yaml        # NEW: ConfigMap with provisioned JSON

scripts/
├── deploy.sh                          # add --set flags for monitoring + adjust CF flags for phase6
└── phase6-pre-deploy-check.sh         # NEW: disk, secrets, tunnel, DNS, Phase 4/5 regression pre-flight

frontend/
└── playwright.config.ts               # baseURL → https://lolday.connlabai.com ; host-resolver-rule updated

docs/
├── phase6-e2e-checklist.md            # NEW: exit criteria + regression + chaos + security checklist
└── superpowers/
    ├── specs/2026-04-20-phase6-operations-design.md  # already committed
    └── plans/2026-04-20-phase6-operations.md          # this file
```

`frontend/` SPA components and all Phase 2/3/4 backend routers are not touched.

---

## Prerequisites (one-time, per-admin)

- [ ] A Cloudflare account exists (free tier is enough). If not, the user creates it at https://dash.cloudflare.com/sign-up.
- [ ] `connlabai.com` is registered and its DNS is managed by Cloudflare (the user has already purchased it).
- [ ] A Google account that can sign in with `@mail.ntust.edu.tw` (the user's own NTUST account) is available for SSO testing.
- [ ] `/mnt/ssd500g/lolday-monitoring` exists, owned by `bolin8017`, 755. Verified with: `ls -ld /mnt/ssd500g/lolday-monitoring` (already satisfied during brainstorming).
- [ ] `~/.lolday-secrets.env` (chmod 600) exists and is sourceable. Contains the existing Phase 4/5 secrets.

---

## Sub-phase 6-1 — Monitoring stack (additive, no external exposure)

### Task 1: Backend `/metrics` instrumentation — TDD

**Goal:** Expose a Prometheus text endpoint at `/metrics` so kube-prometheus-stack can scrape backend. Test first.

**Files:**

- Create: `backend/tests/test_metrics.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/main.py` (add 3 lines immediately after `app = FastAPI(...)` is created; around line ~85)

- [ ] **Step 1: Write the failing test**

Write `backend/tests/test_metrics.py` with:

```python
"""Phase 6: verify /metrics endpoint is exposed for Prometheus scraping."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_endpoint_exists(client: AsyncClient):
    """Metrics endpoint must be publicly reachable inside the cluster."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_content_is_prometheus_format(client: AsyncClient):
    """Content-Type and body must be Prometheus text exposition format."""
    resp = await client.get("/metrics")
    ctype = resp.headers.get("content-type", "")
    # prometheus_client uses text/plain; version=0.0.4
    assert ctype.startswith("text/plain")
    body = resp.text
    # Every Prometheus exposition has at least one HELP/TYPE block
    assert "# HELP" in body
    assert "# TYPE" in body


@pytest.mark.asyncio
async def test_metrics_includes_http_counter(client: AsyncClient):
    """The default instrumentator emits http_requests_total after any request."""
    # Generate at least one request so a counter exists
    await client.get("/api/v1/health")
    resp = await client.get("/metrics")
    # prometheus-fastapi-instrumentator default metric name
    assert "http_requests_total" in resp.text


@pytest.mark.asyncio
async def test_metrics_not_in_openapi_schema(client: AsyncClient):
    """/metrics must NOT appear in OpenAPI — it's an internal endpoint."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/metrics" not in paths
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
uv run pytest tests/test_metrics.py -v
```

Expected: 4 tests fail with `404 Not Found` on `/metrics`.

- [ ] **Step 3: Add dependency**

Edit `backend/pyproject.toml`, add to `dependencies`:

```toml
"prometheus-fastapi-instrumentator~=7.0",
```

Then:

```bash
cd backend
uv sync
```

- [ ] **Step 4: Wire Instrumentator into the FastAPI app**

Edit `backend/app/main.py`. Find the block (around line 75):

```python
app = FastAPI(
    title="Lolday",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
)
```

Immediately below that block, add:

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False,
)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd backend
uv run pytest tests/test_metrics.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the full backend test suite to catch regressions**

```bash
cd backend
uv run pytest -q
```

Expected: all existing tests still pass. The instrumentator adds middleware but does not alter request/response bodies of existing routes.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/test_metrics.py backend/pyproject.toml backend/uv.lock backend/app/main.py
git commit -m "$(cat <<'EOF'
feat(backend): add /metrics endpoint with prometheus-fastapi-instrumentator

Exposes Prometheus text format at /metrics for kube-prometheus-stack
scraping. Uses include_in_schema=False so the endpoint does not leak
into the OpenAPI spec consumed by the frontend generator.
EOF
)"
```

---

### Task 2: Build and push backend `phase6` image

**Goal:** Publish the instrumented backend image to Harbor as `phase6`.

**Files:**

- Modify: `charts/lolday/values.yaml` (bump `backend.image` from `phase5` to `phase6`)

- [ ] **Step 1: Log in to Harbor**

```bash
source ~/.lolday-secrets.env
# Username is the robot account used in Phase 3+; pull password from K8s secret
HARBOR_PUSH_PW=$(kubectl -n lolday get secret harbor-push-cred -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq -r '.auths[].auth' | base64 -d | cut -d: -f2)
docker login harbor.lolday.svc.cluster.local:80 -u 'robot$build-pusher' -p "$HARBOR_PUSH_PW"
```

Expected: `Login Succeeded`.

- [ ] **Step 2: Build**

```bash
cd backend
docker build -t harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase6 .
```

Expected: build succeeds, final image layer created.

- [ ] **Step 3: Push**

```bash
docker push harbor.lolday.svc.cluster.local:80/lolday/lolday-backend:phase6
```

Expected: `digest: sha256:...`.

- [ ] **Step 4: Trigger Trivy scan**

Harbor does not auto-scan on push. Trigger manually:

```bash
ARTIFACT_DIGEST=$(curl -s -u "robot\$build-pusher:$HARBOR_PUSH_PW" \
  "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts?q=tags=phase6" | jq -r '.[0].digest')

curl -sX POST -u "robot\$build-pusher:$HARBOR_PUSH_PW" \
  "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts/$ARTIFACT_DIGEST/scan"
```

Wait ~60 s, then:

```bash
curl -s -u "robot\$build-pusher:$HARBOR_PUSH_PW" \
  "http://harbor.lolday.svc.cluster.local:80/api/v2.0/projects/lolday/repositories/lolday-backend/artifacts/$ARTIFACT_DIGEST?with_scan_overview=true" \
  | jq '.scan_overview'
```

Expected: `scan_status: "Success"`, no `Critical` findings.

- [ ] **Step 5: Bump `values.yaml`**

Edit `charts/lolday/values.yaml`, change:

```yaml
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:phase5
```

to:

```yaml
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:phase6
```

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "chore(backend): bump image to phase6 (adds /metrics)"
```

Note: the new image is not yet deployed — that happens in Task 13. This commit just records the tag choice; Task 13's `helm upgrade` picks it up together with the monitoring stack.

---

### Task 3: Monitoring namespace + custom StorageClass

**Goal:** Create the `monitoring` namespace and a dedicated StorageClass `monitoring-local` backed by `/mnt/ssd500g/lolday-monitoring` so monitoring PVs do not fill the root filesystem.

**Files:**

- Create: `charts/lolday/templates/monitoring/namespace.yaml`
- Create: `charts/lolday/templates/monitoring/storage-class.yaml`

- [ ] **Step 1: Verify the target directory exists and is writable**

```bash
test -d /mnt/ssd500g/lolday-monitoring && echo OK || echo MISSING
test -w /mnt/ssd500g/lolday-monitoring && echo WRITABLE || echo "NOT WRITABLE — fix ownership"
df -h /mnt/ssd500g | tail -1
```

Expected: `OK`, `WRITABLE`, at least 60 Gi free.

- [ ] **Step 2: Create `charts/lolday/templates/monitoring/namespace.yaml`**

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Values.monitoring.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
    monitoring.lolday.io/managed: "true"
{{- end }}
```

- [ ] **Step 3: Create `charts/lolday/templates/monitoring/storage-class.yaml`**

Use `rancher.io/local-path` as the provisioner; pass `nodePath` as a parameter so the local-path-provisioner writes under `/mnt/ssd500g/lolday-monitoring/`:

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: monitoring-local
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
provisioner: rancher.io/local-path
parameters:
  nodePath: {{ .Values.monitoring.storage.nodePath | quote }}
reclaimPolicy: Delete
volumeBindingMode: WaitForFirstConsumer
{{- end }}
```

- [ ] **Step 4: Dry-run render to catch template errors**

Values not yet added to `values.yaml`; pass inline:

```bash
helm template lolday charts/lolday \
  --set monitoring.enabled=true \
  --set monitoring.namespace=monitoring \
  --set monitoring.storage.nodePath=/mnt/ssd500g/lolday-monitoring \
  2>&1 | grep -A 20 'kind: StorageClass'
```

Expected: the StorageClass YAML prints with `nodePath: "/mnt/ssd500g/lolday-monitoring"`.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/monitoring/namespace.yaml charts/lolday/templates/monitoring/storage-class.yaml
git commit -m "feat(charts): add monitoring namespace and StorageClass (phase 6)"
```

---

### Task 4: Add Helm chart dependencies

**Goal:** Register `kube-prometheus-stack`, `loki`, and `promtail` as Helm sub-charts; fetch their archives.

**Files:**

- Modify: `charts/lolday/Chart.yaml`
- Regenerated: `charts/lolday/Chart.lock`
- Ensure: `charts/lolday/.helmignore` ignores vendored sub-chart archives (or keep them; see step 5)

- [ ] **Step 1: Add repositories locally**

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update prometheus-community grafana
```

Expected: `"prometheus-community" has been updated`, `"grafana" has been updated`.

- [ ] **Step 2: Edit `charts/lolday/Chart.yaml`**

Full file content after edit:

```yaml
apiVersion: v2
name: lolday
description: ISLab Malware Detector Management Platform
type: application
version: 0.4.0
appVersion: "0.4.0"

dependencies:
  - name: harbor
    version: "1.16.1"
    repository: https://helm.goharbor.io
    condition: harbor.enabled
  - name: kube-prometheus-stack
    version: "~60.0.0"
    repository: https://prometheus-community.github.io/helm-charts
    alias: kps
    condition: monitoring.enabled
  - name: loki
    version: "~6.0.0"
    repository: https://grafana.github.io/helm-charts
    condition: loki.enabled
  - name: promtail
    version: "~6.0.0"
    repository: https://grafana.github.io/helm-charts
    condition: promtail.enabled
```

Note: the chart version bumps `0.3.0 → 0.4.0` to signal Phase 6 changes.

- [ ] **Step 3: Fetch sub-charts**

```bash
cd charts/lolday
helm dependency update
ls charts/
cd -
```

Expected: `charts/kube-prometheus-stack-60.x.y.tgz`, `charts/loki-6.x.y.tgz`, `charts/promtail-6.x.y.tgz` appear alongside the existing `charts/harbor-1.16.1.tgz`. `Chart.lock` regenerated with new entries.

- [ ] **Step 4: Keep the .tgz files out of git**

Confirm `charts/lolday/.helmignore` already handles runtime packaging ignores; for git, add a `.gitignore` under `charts/lolday/charts/` that ignores the tarballs but keeps the directory:

Edit (create) `charts/lolday/charts/.gitignore`:

```
# Sub-chart tarballs are pulled fresh via `helm dependency update`
*.tgz
```

Verify `charts/lolday/Chart.lock` is NOT ignored (we commit it for reproducibility).

- [ ] **Step 5: Dry-run render to catch template errors**

```bash
helm template lolday charts/lolday \
  --set monitoring.enabled=true \
  --set loki.enabled=true \
  --set promtail.enabled=true \
  > /tmp/phase6-render.yaml 2>&1 | head -50
wc -l /tmp/phase6-render.yaml
```

Expected: thousands of lines rendered; no `Error:` in stderr. (Exact output of `helm template` goes to stdout; any errors appear on stderr.)

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/Chart.yaml charts/lolday/Chart.lock charts/lolday/charts/.gitignore
git commit -m "feat(charts): add kube-prometheus-stack, loki, promtail dependencies"
```

---

### Task 5: `kube-prometheus-stack` values block

**Goal:** Configure the monitoring sub-chart — Prometheus retention, storage, Grafana admin secret reference, Alertmanager with empty receivers, enable auto-discovery of all ServiceMonitors regardless of release label.

**Files:**

- Modify: `charts/lolday/values.yaml` (append a `monitoring` top-level block and a `kps` sub-chart block)

- [ ] **Step 1: Append the top-level monitoring config to `values.yaml`**

At the end of `charts/lolday/values.yaml`, append:

```yaml
# =============================================================================
# Monitoring (Phase 6) — kube-prometheus-stack + loki + promtail
# =============================================================================
monitoring:
  enabled: true
  namespace: monitoring
  storage:
    # Dedicated NVMe partition so monitoring doesn't crowd the K3s root FS
    nodePath: /mnt/ssd500g/lolday-monitoring
```

- [ ] **Step 2: Append the kube-prometheus-stack sub-chart config**

Continuing in `charts/lolday/values.yaml`, append:

```yaml
# Sub-chart config consumed as values of the kube-prometheus-stack chart (alias: kps)
kps:
  namespaceOverride: monitoring
  fullnameOverride: kps
  defaultRules:
    create: true

  prometheus:
    prometheusSpec:
      retention: 15d
      retentionSize: 18GiB # leave ~10% head-room under 20 Gi PVC
      # Scrape every ServiceMonitor in the cluster (default scopes to the Helm release label)
      serviceMonitorSelectorNilUsesHelmValues: false
      podMonitorSelectorNilUsesHelmValues: false
      probeSelectorNilUsesHelmValues: false
      ruleSelectorNilUsesHelmValues: false
      storageSpec:
        volumeClaimTemplate:
          spec:
            storageClassName: monitoring-local
            accessModes: [ReadWriteOnce]
            resources:
              requests:
                storage: 20Gi
      resources:
        requests: { cpu: 500m, memory: 2Gi }
        limits: { cpu: 2, memory: 8Gi }

  alertmanager:
    alertmanagerSpec:
      storage:
        volumeClaimTemplate:
          spec:
            storageClassName: monitoring-local
            accessModes: [ReadWriteOnce]
            resources:
              requests:
                storage: 2Gi
    # Empty receivers: alerts show up in Alertmanager UI but nothing is sent.
    # Wiring Resend/Discord is Phase 7.
    config:
      route:
        receiver: "null"
        group_wait: 10s
        group_interval: 5m
        repeat_interval: 12h
      receivers:
        - name: "null"

  grafana:
    persistence:
      enabled: true
      storageClassName: monitoring-local
      size: 5Gi
    # Credentials come from the Secret we create in templates/monitoring/grafana-admin-secret.yaml
    admin:
      existingSecret: grafana-admin
      userKey: admin-user
      passwordKey: admin-password
    defaultDashboardsEnabled: true
    sidecar:
      dashboards:
        enabled: true
        label: grafana_dashboard
        searchNamespace: ALL
      datasources:
        enabled: true
    resources:
      requests: { cpu: 100m, memory: 256Mi }
      limits: { cpu: 500m, memory: 1Gi }

  kubeStateMetrics: { enabled: true }
  nodeExporter:
    enabled: true

  # Disable ServiceMonitors that try to scrape K3s-hidden components (apiserver, scheduler, controller-manager).
  # K3s runs these as embedded goroutines inside the server binary; their metrics are exposed on
  # 127.0.0.1:10259/10257 which Prometheus cannot reach. Disabling silences noisy "DOWN" alerts.
  kubeApiServer: { enabled: true }
  kubeControllerManager: { enabled: false }
  kubeScheduler: { enabled: false }
  kubeProxy: { enabled: false }
  kubeEtcd: { enabled: false }
```

- [ ] **Step 3: Dry-run render**

```bash
helm template lolday charts/lolday --set cloudflare.enabled=false 2>&1 \
  | grep -E "^kind:|serviceMonitorSelectorNilUsesHelmValues" | head -40
```

Expected: `kind: Prometheus`, `kind: Alertmanager`, `kind: ServiceMonitor` appear; no rendering errors.

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "feat(charts): add kube-prometheus-stack values (phase 6)"
```

---

### Task 6: Loki + Promtail values blocks

**Goal:** Configure Loki single-binary mode and Promtail DaemonSet.

**Files:**

- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Append Loki and Promtail blocks to `values.yaml`**

```yaml
# =============================================================================
# Loki — log aggregation (single-binary mode)
# =============================================================================
loki:
  enabled: true
  # Top-level 'loki' key is consumed by the loki sub-chart directly
  deploymentMode: SingleBinary
  loki:
    commonConfig:
      replication_factor: 1
    schemaConfig:
      configs:
        - from: 2024-04-01
          store: tsdb
          object_store: filesystem
          schema: v13
          index:
            prefix: loki_index_
            period: 24h
    storage:
      type: filesystem
      bucketNames:
        chunks: loki-chunks
        ruler: loki-ruler
        admin: loki-admin
    auth_enabled: false
    limits_config:
      retention_period: 168h # 7 days
      reject_old_samples: true
      reject_old_samples_max_age: 168h
  singleBinary:
    replicas: 1
    persistence:
      enabled: true
      storageClass: monitoring-local
      size: 30Gi
    resources:
      requests: { cpu: 200m, memory: 512Mi }
      limits: { cpu: 1, memory: 2Gi }
  # Scalable-mode components off
  read: { replicas: 0 }
  write: { replicas: 0 }
  backend: { replicas: 0 }
  # No ingress — internal only
  gateway:
    enabled: false
  # ChunksCache / ResultsCache are not needed at lab scale
  chunksCache: { enabled: false }
  resultsCache: { enabled: false }

# =============================================================================
# Promtail — DaemonSet that ships container logs to Loki
# =============================================================================
promtail:
  enabled: true
  config:
    clients:
      - url: http://loki.monitoring.svc:3100/loki/api/v1/push
    snippets:
      pipelineStages:
        - cri: {}
      extraRelabelConfigs:
        - source_labels:
            [__meta_kubernetes_pod_label_app_kubernetes_io_component]
          target_label: component
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits: { cpu: 200m, memory: 256Mi }
  serviceMonitor:
    enabled: true
    namespace: monitoring
```

- [ ] **Step 2: Dry-run render**

```bash
helm template lolday charts/lolday 2>&1 | grep -cE "^kind:"
```

Expected: a large integer (hundreds) — confirming both sub-charts render.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/values.yaml
git commit -m "feat(charts): add loki + promtail values (phase 6)"
```

---

### Task 7: Grafana admin Secret template

**Goal:** Provide the `grafana-admin` Secret referenced by the `kps` chart so Grafana boots with a known password managed via `~/.lolday-secrets.env`.

**Files:**

- Create: `charts/lolday/templates/monitoring/grafana-admin-secret.yaml`

- [ ] **Step 1: Create the Secret template**

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: grafana-admin
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  admin-user: admin
  admin-password: {{ required "monitoring.grafana.adminPassword must be set (via --set or values)" .Values.monitoring.grafana.adminPassword | quote }}
{{- end }}
```

- [ ] **Step 2: Append the value placeholder to `values.yaml`**

Extend the existing `monitoring:` block so it includes the admin-password key (left blank; fed via `--set`):

```yaml
monitoring:
  enabled: true
  namespace: monitoring
  storage:
    nodePath: /mnt/ssd500g/lolday-monitoring
  grafana:
    adminPassword: "" # --set at deploy time, NEVER commit
```

(Replace the earlier short `monitoring:` block with this one.)

- [ ] **Step 3: Dry-run render to verify the `required` check fires**

```bash
helm template lolday charts/lolday 2>&1 | tail -5
```

Expected: error with message starting with `Error: execution error ... monitoring.grafana.adminPassword must be set`.

- [ ] **Step 4: Dry-run with value**

```bash
helm template lolday charts/lolday --set monitoring.grafana.adminPassword=testpw 2>&1 | grep -A 8 "kind: Secret" | grep -A 6 grafana-admin
```

Expected: Secret renders with `admin-user: admin` and `admin-password: "testpw"`.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/monitoring/grafana-admin-secret.yaml charts/lolday/values.yaml
git commit -m "feat(charts): grafana-admin Secret + adminPassword values placeholder"
```

---

### Task 8: Postgres exporter — Deployment, Service, DB user init Job, Secret

**Goal:** Deploy `postgres-exporter` as a standalone Deployment in the `lolday` namespace, create the `postgres_exporter` DB user with `pg_monitor` role (once, via a Helm Job), and expose port 9187 behind a Service.

**Files:**

- Create: `charts/lolday/templates/monitoring/postgres-exporter.yaml`
- Create: `charts/lolday/templates/monitoring/postgres-exporter-initjob.yaml`
- Modify: `charts/lolday/values.yaml` (add `monitoring.postgresExporter` block)

- [ ] **Step 1: Extend `values.yaml`**

Locate the existing `monitoring:` block (created in Task 7) and append a `postgresExporter` subkey so the block reads:

```yaml
monitoring:
  enabled: true
  namespace: monitoring
  storage:
    nodePath: /mnt/ssd500g/lolday-monitoring
  grafana:
    adminPassword: "" # --set at deploy time (from Task 7)
  postgresExporter:
    enabled: true
    image: quay.io/prometheuscommunity/postgres-exporter:v0.17.0
    password: "" # --set at deploy time, NEVER commit
    resources:
      requests: { cpu: 20m, memory: 64Mi }
      limits: { cpu: 100m, memory: 128Mi }
```

- [ ] **Step 2: Create `charts/lolday/templates/monitoring/postgres-exporter.yaml`**

```yaml
{{- if and .Values.monitoring.enabled .Values.monitoring.postgresExporter.enabled }}
---
apiVersion: v1
kind: Secret
metadata:
  name: postgres-exporter-db
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  DATA_SOURCE_NAME: "postgresql://postgres_exporter:{{ required "monitoring.postgresExporter.password must be set" .Values.monitoring.postgresExporter.password }}@postgresql.{{ .Values.global.namespace }}.svc:5432/lolday?sslmode=disable"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-exporter
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/name: postgres-exporter
    app.kubernetes.io/component: postgres-exporter
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres-exporter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgres-exporter
        app.kubernetes.io/component: postgres-exporter
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 65534
        runAsGroup: 65534
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: exporter
          image: {{ .Values.monitoring.postgresExporter.image | quote }}
          args:
            - "--web.listen-address=:9187"
            - "--collector.database"
            - "--collector.stat_activity_autovacuum"
          envFrom:
            - secretRef:
                name: postgres-exporter-db
          ports:
            - name: metrics
              containerPort: 9187
          readinessProbe:
            httpGet: { path: /, port: metrics }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /, port: metrics }
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            {{- toYaml .Values.monitoring.postgresExporter.resources | nindent 12 }}
          securityContext:
            readOnlyRootFilesystem: true
            allowPrivilegeEscalation: false
            capabilities: { drop: [ALL] }
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-exporter
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/name: postgres-exporter
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  selector:
    app.kubernetes.io/name: postgres-exporter
  ports:
    - name: metrics
      port: 9187
      targetPort: metrics
      protocol: TCP
{{- end }}
```

- [ ] **Step 3: Create `charts/lolday/templates/monitoring/postgres-exporter-initjob.yaml`**

This Job runs on `post-install,post-upgrade`, idempotent: creates the `postgres_exporter` user only if it does not exist.

```yaml
{{- if and .Values.monitoring.enabled .Values.monitoring.postgresExporter.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: postgres-exporter-init-{{ randAlphaNum 6 | lower }}
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": post-install,post-upgrade
    "helm.sh/hook-weight": "5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: 3
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgres-exporter-init
    spec:
      restartPolicy: OnFailure
      containers:
        - name: init
          image: postgres:16-alpine
          env:
            - name: PGHOST
              value: postgresql.{{ .Values.global.namespace }}.svc
            - name: PGUSER
              value: lolday
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: postgresql
                  key: postgres-password
            - name: EXPORTER_PW
              value: {{ required "monitoring.postgresExporter.password must be set" .Values.monitoring.postgresExporter.password | quote }}
          command:
            - /bin/sh
            - -c
            - |
              set -eu
              psql -v ON_ERROR_STOP=1 -d postgres <<SQL
              DO \$\$
              BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres_exporter') THEN
                  CREATE USER postgres_exporter WITH PASSWORD '${EXPORTER_PW}';
                ELSE
                  ALTER USER postgres_exporter WITH PASSWORD '${EXPORTER_PW}';
                END IF;
              END
              \$\$;
              GRANT pg_monitor TO postgres_exporter;
              SQL
{{- end }}
```

Note: the `postgresql` Secret key for the `lolday` superuser is `postgres-password` (set by the Bitnami PostgreSQL chart / our existing StatefulSet). Verify with `kubectl -n lolday get secret postgresql -o jsonpath='{.data}' | jq` if unsure.

- [ ] **Step 4: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set monitoring.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=y 2>&1 \
  | grep -A 2 "name: postgres-exporter\b"
```

Expected: Deployment, Service, Secret all render.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/monitoring/postgres-exporter.yaml \
        charts/lolday/templates/monitoring/postgres-exporter-initjob.yaml \
        charts/lolday/values.yaml
git commit -m "feat(charts): postgres-exporter Deployment + init Job (phase 6)"
```

---

### Task 9: ServiceMonitors for backend, Traefik, Harbor, postgres-exporter

**Goal:** Tell Prometheus (via kube-prometheus-stack CRDs) which Services to scrape inside the `lolday` and `kube-system` namespaces.

**Files:**

- Create: `charts/lolday/templates/monitoring/servicemonitor-backend.yaml`
- Create: `charts/lolday/templates/monitoring/servicemonitor-traefik.yaml`
- Create: `charts/lolday/templates/monitoring/servicemonitor-harbor.yaml`
- Create: `charts/lolday/templates/monitoring/servicemonitor-postgres.yaml`

- [ ] **Step 1: Backend ServiceMonitor**

The backend Service (`charts/lolday/templates/backend.yaml` ~line 90) exposes port 8000 unnamed, so target it by `targetPort` instead of port name.

Create `charts/lolday/templates/monitoring/servicemonitor-backend.yaml`:

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: backend
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  namespaceSelector:
    matchNames: [{{ .Values.global.namespace }}]
  selector:
    matchLabels:
      app.kubernetes.io/component: backend
  endpoints:
    - targetPort: 8000
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
{{- end }}
```

- [ ] **Step 2: Traefik ServiceMonitor**

K3s's bundled Traefik runs with `--metrics.prometheus=true` by default and serves `/metrics` on pod port 9100. The Service only exposes `web` (80) and `websecure` (443), so the ServiceMonitor targets the pod port directly with `targetPort`.

Create `charts/lolday/templates/monitoring/servicemonitor-traefik.yaml`:

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: traefik
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  namespaceSelector:
    matchNames: [kube-system]
  selector:
    matchLabels:
      app.kubernetes.io/name: traefik
  endpoints:
    - targetPort: 9100
      path: /metrics
      interval: 30s
{{- end }}
```

Verify after deploy (in Task 13) that Traefik's pod actually listens on 9100:

```bash
kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].spec.containers[0].args}'
```

Expected: the `args` array contains `--metrics.prometheus=true` and `--entryPoints.metrics.address=:9100`. If not, a K3s HelmChartConfig in `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` is needed (documented in Task 13 Step 1 troubleshooting).

- [ ] **Step 3: Harbor metrics — use Harbor chart's built-in ServiceMonitor**

The Harbor subchart ships a ServiceMonitor of its own when `metrics.enabled=true` and `metrics.serviceMonitor.enabled=true`. That is cleaner than writing our own because the chart knows the exact port layout (core/jobservice/registry/exporter all expose `/metrics` on port 8001 when metrics is enabled). Do NOT create `servicemonitor-harbor.yaml`.

In `charts/lolday/values.yaml`, extend the existing `harbor:` block with:

```yaml
harbor:
  # ... existing keys unchanged ...
  metrics:
    enabled: true
    serviceMonitor:
      enabled: true
      additionalLabels:
        release: lolday # matches kps Prometheus selector
      interval: 60s
```

No `servicemonitor-harbor.yaml` file is needed.

- [ ] **Step 4: Postgres-exporter ServiceMonitor**

Create `charts/lolday/templates/monitoring/servicemonitor-postgres.yaml`:

```yaml
{{- if and .Values.monitoring.enabled .Values.monitoring.postgresExporter.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: postgres-exporter
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  namespaceSelector:
    matchNames: [{{ .Values.global.namespace }}]
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres-exporter
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
{{- end }}
```

- [ ] **Step 5: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set monitoring.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=y 2>&1 \
  | grep -c "kind: ServiceMonitor"
```

Expected: ≥ 4.

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/templates/monitoring/servicemonitor-backend.yaml \
        charts/lolday/templates/monitoring/servicemonitor-traefik.yaml \
        charts/lolday/templates/monitoring/servicemonitor-postgres.yaml \
        charts/lolday/values.yaml
git commit -m "feat(charts): ServiceMonitors for backend/traefik/postgres + enable Harbor SM"
```

---

### Task 10: Alertmanager rules

**Goal:** Define four baseline alerts visible in Alertmanager UI even with empty receivers.

**Files:**

- Create: `charts/lolday/templates/monitoring/alertmanager-rules.yaml`

- [ ] **Step 1: Create the PrometheusRule**

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: lolday-baseline
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    app: kube-prometheus-stack
    release: lolday
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  groups:
    - name: lolday-baseline.rules
      interval: 30s
      rules:
        - alert: NodeDiskAlmostFull
          expr: |
            (node_filesystem_size_bytes{mountpoint=~"/|/mnt/ssd500g"}
             - node_filesystem_avail_bytes{mountpoint=~"/|/mnt/ssd500g"})
             / node_filesystem_size_bytes{mountpoint=~"/|/mnt/ssd500g"} > 0.85
          for: 10m
          labels:
            severity: critical
          annotations:
            summary: "Node disk > 85% on {{`{{ $labels.instance }}`}} ({{`{{ $labels.mountpoint }}`}})"
            description: "Filesystem {{`{{ $labels.mountpoint }}`}} on {{`{{ $labels.instance }}`}} is above 85% used for 10m."

        - alert: GPUTemperatureHigh
          expr: DCGM_FI_DEV_GPU_TEMP > 85
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "GPU {{`{{ $labels.gpu }}`}} on {{`{{ $labels.Hostname }}`}} exceeded 85°C"
            description: "GPU temperature > 85°C for 5m on {{`{{ $labels.Hostname }}`}}."

        - alert: PodCrashLoopBackOff
          expr: kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} == 1
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "Pod {{`{{ $labels.namespace }}`}}/{{`{{ $labels.pod }}`}} is CrashLoopBackOff"
            description: "Container {{`{{ $labels.container }}`}} in pod {{`{{ $labels.namespace }}`}}/{{`{{ $labels.pod }}`}} has been CrashLoopBackOff for 5m."

        - alert: LoldayCoreServiceDown
          expr: up{job=~"backend|postgres-exporter|harbor"} == 0
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "Core service {{`{{ $labels.job }}`}} is down"
            description: "Prometheus has not been able to scrape {{`{{ $labels.job }}`}} for 2m."
{{- end }}
```

Note: the `app: kube-prometheus-stack` + `release: lolday` labels are how kube-prometheus-stack's default `ruleSelector` picks up the rule; matching these labels avoids needing the `ruleSelectorNilUsesHelmValues: false` workaround.

- [ ] **Step 2: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set monitoring.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=y 2>&1 \
  | grep -A 2 "kind: PrometheusRule"
```

Expected: one PrometheusRule renders with name `lolday-baseline`.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/monitoring/alertmanager-rules.yaml
git commit -m "feat(charts): baseline Prometheus alert rules (phase 6)"
```

---

### Task 11: Grafana dashboards (auto-provisioned via ConfigMap)

**Goal:** Preload three community dashboards so Grafana boots with DCGM, Traefik, and PostgreSQL views ready.

**Files:**

- Create: `charts/lolday/templates/monitoring/grafana-dashboards.yaml`
- Create (downloaded, checked in): `charts/lolday/dashboards/dcgm.json`, `traefik.json`, `postgresql.json`

- [ ] **Step 1: Download dashboards**

```bash
mkdir -p charts/lolday/dashboards
# DCGM exporter
curl -sL "https://grafana.com/api/dashboards/12239/revisions/latest/download" -o charts/lolday/dashboards/dcgm.json
# Traefik 3 (id 17346 targets v3)
curl -sL "https://grafana.com/api/dashboards/17346/revisions/latest/download" -o charts/lolday/dashboards/traefik.json
# PostgreSQL Database by the prometheus-community collector (id 9628)
curl -sL "https://grafana.com/api/dashboards/9628/revisions/latest/download" -o charts/lolday/dashboards/postgresql.json
# Sanity check
wc -l charts/lolday/dashboards/*.json
```

Expected: each file is ≥ 100 lines of JSON. If any download returns `{"error":...}`, look up the current revision on grafana.com and adjust.

- [ ] **Step 2: Normalize the `__inputs` placeholders**

Grafana Community JSON frequently has `${DS_PROMETHEUS}` or `${DS_LOKI}` as the datasource variable; kube-prometheus-stack's sidecar resolves these to the default datasource automatically, so no edits are needed unless a dashboard hardcodes a different datasource name. Open each JSON and verify `"datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}` or similar. If a dashboard uses a literal UID (e.g. `"prometheus"`), either leave it (sidecar handles) or change to `${DS_PROMETHEUS}`.

- [ ] **Step 3: Create the ConfigMap template**

```yaml
{{- if .Values.monitoring.enabled }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards-phase6
  namespace: {{ .Values.monitoring.namespace }}
  labels:
    grafana_dashboard: "1"          # Triggers kube-prometheus-stack Grafana sidecar to pick it up
    {{- include "lolday.labels" . | nindent 4 }}
data:
  dcgm.json: |-
    {{ .Files.Get "dashboards/dcgm.json" | nindent 4 }}
  traefik.json: |-
    {{ .Files.Get "dashboards/traefik.json" | nindent 4 }}
  postgresql.json: |-
    {{ .Files.Get "dashboards/postgresql.json" | nindent 4 }}
{{- end }}
```

- [ ] **Step 4: Handle ConfigMap size limit**

A K8s ConfigMap entry must be ≤ 1 MiB (total Secret/ConfigMap ≤ 1 MiB). Grafana dashboards are typically < 200 KiB each; three in one ConfigMap is safely under the limit. Verify:

```bash
du -b charts/lolday/dashboards/*.json | awk '{s += $1} END { print s " bytes total" }'
```

Expected: well under 1,048,576 bytes.

- [ ] **Step 5: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set monitoring.grafana.adminPassword=x \
  --set monitoring.postgresExporter.password=y 2>&1 \
  | grep -A 2 "name: grafana-dashboards-phase6"
```

Expected: ConfigMap renders with three keys (dcgm.json, traefik.json, postgresql.json).

- [ ] **Step 6: Commit**

```bash
git add charts/lolday/dashboards/*.json charts/lolday/templates/monitoring/grafana-dashboards.yaml
git commit -m "feat(charts): provision Grafana dashboards for DCGM/Traefik/PostgreSQL"
```

---

### Task 12: `phase6-pre-deploy-check.sh` + `deploy.sh` extensions

**Goal:** A single script the admin runs before every Phase 6 deploy that validates all prerequisites. Extend `deploy.sh` to generate + pass the new secrets.

**Files:**

- Create: `scripts/phase6-pre-deploy-check.sh`
- Modify: `scripts/deploy.sh`

- [ ] **Step 1: Create `scripts/phase6-pre-deploy-check.sh`**

```bash
#!/usr/bin/env bash
# Phase 6 pre-deploy check: verify disk, secrets, and Phase 4/5 still work.
set -euo pipefail

echo "=== Phase 6 pre-deploy check ==="

# --- Disk space ---
echo "[1/6] /mnt/ssd500g/lolday-monitoring ..."
if [ ! -d /mnt/ssd500g/lolday-monitoring ]; then
  echo "  MISSING — run: sudo mkdir -p /mnt/ssd500g/lolday-monitoring && sudo chown \$USER:\$USER \$_"
  exit 1
fi
if [ ! -w /mnt/ssd500g/lolday-monitoring ]; then
  echo "  NOT WRITABLE — fix ownership"
  exit 1
fi
FREE_G=$(df -BG --output=avail /mnt/ssd500g | tail -1 | tr -d ' G')
if [ "$FREE_G" -lt 60 ]; then
  echo "  FREE $FREE_G Gi — < 60 Gi required"
  exit 1
fi
echo "  OK ($FREE_G Gi free)"

# --- Secrets ---
echo "[2/6] secrets in env ..."
: "${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD in ~/.lolday-secrets.env}"
: "${PG_EXPORTER_PASSWORD:?set PG_EXPORTER_PASSWORD in ~/.lolday-secrets.env}"
echo "  OK"

# --- Tunnel + DNS (only strict for sub-phase 6-3) ---
echo "[3/6] Tunnel config ..."
if [ "${CF_ENABLED:-false}" = "true" ]; then
  : "${CF_TUNNEL_TOKEN:?CF_ENABLED=true but CF_TUNNEL_TOKEN not set}"
  # DNS check: CNAME should resolve via Cloudflare
  if ! dig +short lolday.connlabai.com | grep -q cfargotunnel.com; then
    echo "  WARN: lolday.connlabai.com does not resolve to cfargotunnel.com yet"
  fi
fi
echo "  OK"

# --- Phase 4 backend health ---
echo "[4/6] backend reachable via in-cluster port-forward ..."
PF_PID=""
cleanup() { [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null || true; }
trap cleanup EXIT
kubectl -n lolday port-forward svc/backend 18999:8000 >/dev/null 2>&1 &
PF_PID=$!
sleep 2
if ! curl -fsS http://localhost:18999/docs >/dev/null; then
  echo "  FAIL — backend not responding"
  exit 1
fi
echo "  OK"

# --- Phase 5 frontend health ---
echo "[5/6] frontend pod Ready ..."
READY=$(kubectl -n lolday get pod -l app.kubernetes.io/component=frontend -o jsonpath='{.items[0].status.containerStatuses[0].ready}')
if [ "$READY" != "true" ]; then
  echo "  FAIL — frontend pod not Ready"
  exit 1
fi
echo "  OK"

# --- Chart lint ---
echo "[6/6] helm lint ..."
helm lint "$(dirname "$0")/../charts/lolday" \
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  --set monitoring.postgresExporter.password="$PG_EXPORTER_PASSWORD" \
  >/dev/null
echo "  OK"

echo ""
echo "=== All checks passed ==="
```

```bash
chmod +x scripts/phase6-pre-deploy-check.sh
```

- [ ] **Step 2: Generate passwords and add them to `~/.lolday-secrets.env`**

```bash
grep -q GRAFANA_ADMIN_PASSWORD ~/.lolday-secrets.env || \
  echo "export GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/')" >> ~/.lolday-secrets.env

grep -q PG_EXPORTER_PASSWORD ~/.lolday-secrets.env || \
  echo "export PG_EXPORTER_PASSWORD=$(openssl rand -base64 32 | tr -d '=+/')" >> ~/.lolday-secrets.env

source ~/.lolday-secrets.env
env | grep -E "GRAFANA_ADMIN_PASSWORD|PG_EXPORTER_PASSWORD" | sed 's/=.*/=***REDACTED***/'
```

Expected: both variables appear (masked) in the output.

- [ ] **Step 3: Extend `scripts/deploy.sh`**

In `scripts/deploy.sh`, find the block that requires secrets (around line 10–18). Add after `MLFLOW_DB_PASSWORD` line:

```bash
: "${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD must be set — generate with: openssl rand -base64 32 | tr -d '=+/'}"
: "${PG_EXPORTER_PASSWORD:?PG_EXPORTER_PASSWORD must be set — generate with: openssl rand -base64 32 | tr -d '=+/'}"
```

In the `helm upgrade --install` command, append these flags immediately before `--wait`:

```bash
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  --set monitoring.postgresExporter.password="$PG_EXPORTER_PASSWORD" \
```

Also bump the default backend image tag:

```bash
BACKEND_IMAGE=${BACKEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-backend:phase6}
```

- [ ] **Step 4: Run the pre-deploy check**

```bash
source ~/.lolday-secrets.env
bash scripts/phase6-pre-deploy-check.sh
```

Expected: "All checks passed". If a check fails, fix the environment per its message and re-run.

- [ ] **Step 5: Commit**

```bash
git add scripts/phase6-pre-deploy-check.sh scripts/deploy.sh
git commit -m "feat(scripts): phase6 pre-deploy check + deploy.sh extensions"
```

---

### Task 13: Deploy sub-phase 6-1 + verify

**Goal:** Roll out the monitoring stack. No external exposure changes yet.

- [ ] **Step 1: Verify Traefik prometheus metrics are enabled (no service patch needed)**

Our Traefik ServiceMonitor hits pod port 9100 directly (see Task 9 Step 2); no Service port patch required.

Confirm Traefik is listening on that port:

```bash
kubectl -n kube-system get pod -l app.kubernetes.io/name=traefik -o jsonpath='{.items[0].spec.containers[0].args}' | tr ',' '\n' | grep -E "metrics|prometheus"
```

Expected: at least `--metrics.prometheus=true` and `--entryPoints.metrics.address=:9100`.

If not present, K3s's Traefik is not serving metrics. Fix: ask the admin to apply a HelmChartConfig at `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` (requires root). K3s watches this directory and applies overrides automatically:

```yaml
# /var/lib/rancher/k3s/server/manifests/traefik-config.yaml
apiVersion: helm.cattle.io/v1
kind: HelmChartConfig
metadata:
  name: traefik
  namespace: kube-system
spec:
  valuesContent: |-
    metrics:
      prometheus:
        enabled: true
        entryPoint: metrics
    additionalArguments:
      - --entryPoints.metrics.address=:9100
```

Skip if already enabled. If applied, wait ~30 s for K3s to reconcile, then re-check the pod's args.

- [ ] **Step 2: Run the deploy**

```bash
source ~/.lolday-secrets.env
bash scripts/deploy.sh
```

Expected: `helm upgrade --install` completes within ~10 min; `Deploy complete` printed.

- [ ] **Step 3: Wait for monitoring pods**

```bash
kubectl -n monitoring get pods -w
```

Watch until every pod shows `Running` and `1/1` or `2/2` Ready. Ctrl-C when all are ready. Expected pods:

- `kps-kube-prometheus-stack-operator-*`
- `prometheus-kps-kube-prometheus-stack-prometheus-0`
- `alertmanager-kps-kube-prometheus-stack-alertmanager-0`
- `kps-kube-prometheus-stack-grafana-*`
- `kps-kube-state-metrics-*`
- `kps-prometheus-node-exporter-*` (DaemonSet)
- `loki-0`
- `promtail-*` (DaemonSet)

- [ ] **Step 4: Verify Grafana is reachable**

```bash
kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-grafana 3000:80 &
GRAFANA_PF=$!
sleep 3
curl -sI http://localhost:3000/login | head -1
```

Expected: `HTTP/1.1 200 OK`. Open a browser to `http://localhost:3000`, log in with `admin` / `$GRAFANA_ADMIN_PASSWORD`.

Check:

- Dashboard "Kubernetes / Compute Resources / Cluster" shows data
- Dashboard "NVIDIA DCGM Exporter Dashboard" (from the ConfigMap) shows 2 GPUs
- Dashboard "Traefik 3" shows request counters (may be empty if no traffic yet)
- Dashboard "PostgreSQL Database" shows connections

Close the port-forward: `kill $GRAFANA_PF`.

- [ ] **Step 5: Verify Loki has logs**

```bash
kubectl -n monitoring port-forward svc/loki 3100:3100 &
LOKI_PF=$!
sleep 3
# LogQL: everything from namespace lolday in the last 5 minutes
curl -sG --data-urlencode 'query={namespace="lolday"}' \
  --data 'limit=5' \
  "http://localhost:3100/loki/api/v1/query_range?start=$(($(date +%s) - 300))000000000&end=$(date +%s)000000000" \
  | jq '.data.result | length'
kill $LOKI_PF
```

Expected: ≥ 1 (at least one stream from lolday namespace).

- [ ] **Step 6: Verify Prometheus scrape targets are up**

```bash
kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-prometheus 9090:9090 &
PROM_PF=$!
sleep 3
curl -sG "http://localhost:9090/api/v1/targets" \
  | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.health)"' \
  | sort -u
kill $PROM_PF
```

Expected: ≥ 10 distinct `job`, all with `health: up` (a few may be `down` the first minute — rerun after 60 s if so). Backend, postgres-exporter, harbor, node-exporter, kube-state-metrics, kubelet, dcgm-exporter should be present.

- [ ] **Step 7: Verify alert rules loaded**

```bash
kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-alertmanager 9093:9093 &
AM_PF=$!
sleep 3
curl -s "http://localhost:9093/api/v2/alerts" | jq 'length'
# also check rules in Prometheus
curl -s "http://localhost:9090/api/v1/rules" | jq -r '.data.groups[] | select(.name=="lolday-baseline.rules") | .rules[].name'
kill $AM_PF
```

Expected: four rule names printed (`NodeDiskAlmostFull`, `GPUTemperatureHigh`, `PodCrashLoopBackOff`, `LoldayCoreServiceDown`).

- [ ] **Step 8: Phase 4 regression**

Using the existing `docs/phase4-e2e-checklist.md`, run a short curl-based smoke:

```bash
kubectl -n lolday port-forward svc/backend 8000:8000 &
BE_PF=$!
sleep 2
TOKEN=$(curl -sX POST http://localhost:8000/api/v1/auth/login \
  -d "username=$ADMIN_EMAIL&password=$ADMIN_PASSWORD" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)
curl -sH "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/users/me | jq .email
curl -sH "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/detectors | jq 'length'
kill $BE_PF
```

Expected: email matches `$ADMIN_EMAIL`; detectors list returns a number (0 acceptable).

- [ ] **Step 9: Phase 5 regression**

```bash
cd frontend
pnpm install
pnpm exec playwright test
cd ..
```

Expected: all 5 specs pass. (If one fails because of new backend `/metrics` endpoint, see Task 14 troubleshooting.)

- [ ] **Step 10: Commit any deploy.sh adjustments + update memory notes**

```bash
git status
# If deploy.sh needed Traefik patch codified, commit that here.
# git add scripts/deploy.sh
# git commit -m "chore(deploy): codify traefik metrics port patch"
```

---

## Sub-phase 6-2 — Cloudflare Access policy (dashboard-only)

These are manual steps in Cloudflare dashboard. No cluster change.

### Task 14: Create Cloudflare Tunnel + Access application

**Goal:** Prepare the Access policy and tunnel identity before any traffic is routed.

- [ ] **Step 1: Create Tunnel**

Open Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create a tunnel → `Cloudflared` type → name it `lolday-server30` → Save. Copy the Tunnel Token (starts with `eyJhIjoi...`).

- [ ] **Step 2: Persist the token**

```bash
grep -q CF_TUNNEL_TOKEN ~/.lolday-secrets.env || \
  echo 'export CF_TUNNEL_TOKEN="<paste-token-here>"' >> ~/.lolday-secrets.env
echo 'export CF_ENABLED=true' >> ~/.lolday-secrets.env
source ~/.lolday-secrets.env
env | grep CF_ | sed 's/=.*/=***REDACTED***/'
```

Paste the token in place of `<paste-token-here>`.

- [ ] **Step 3: Do NOT add a Public Hostname yet**

Skip the "Public Hostname" step of the tunnel wizard (that step is Task 16). Save & exit.

- [ ] **Step 4: Configure Google IdP**

Zero Trust → Settings → Authentication → Login methods → Add new → Google. Follow the OAuth consent screen. Name it `Google (NTUST)`. Save.

- [ ] **Step 5: Create Access Application**

Zero Trust → Access → Applications → Add an application → Self-hosted. Fill:

- Application name: `lolday`
- Session Duration: `24h`
- Application Domain: `lolday.connlabai.com`
- App Launcher Visibility: off (optional)

Save & continue.

- [ ] **Step 6: Create policy**

Policy:

- Policy name: `NTUST staff`
- Action: `Allow`
- Session duration: default
- Configure rules:
  - Include: `Emails ending in` → `@mail.ntust.edu.tw`
  - Include: Selector `Login Methods` = `Google (NTUST)`

Save. Continue through remaining wizard steps (no cookie / CORS overrides), Save final.

- [ ] **Step 7: Verify the policy is active**

```bash
# From the admin's laptop (not server30); requires cloudflared CLI
cloudflared access login https://lolday.connlabai.com
```

Expected: opens a browser, prompts Google sign-in, accepts only `@mail.ntust.edu.tw`. A non-NTUST Google account sees "You do not have access".

Skip this step if cloudflared CLI is not installed on the admin laptop; Task 17 re-verifies end-to-end.

- [ ] **Step 8: Note to self: no commit here**

Everything in Task 14 lives in Cloudflare dashboard. No code commit. Update `~/.lolday-secrets.env` only.

---

## Sub-phase 6-3 — Enable Tunnel + switch host

### Task 15: NetworkPolicy restricting cloudflared egress

**Goal:** Even if `cloudflared` container is compromised, its outbound traffic is bounded to DNS, Traefik, and Cloudflare edge.

**Files:**

- Create: `charts/lolday/templates/netpol-cloudflared.yaml`

- [ ] **Step 1: Create the NetworkPolicy**

```yaml
{{- if .Values.cloudflare.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cloudflared-egress
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: cloudflared
  policyTypes: [Egress]
  egress:
    # DNS
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # In-cluster Traefik Service
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: traefik
      ports:
        - protocol: TCP
          port: 80
        - protocol: TCP
          port: 443
    # Cloudflare edge (IP allowlist; the full list is long — allow any destination on :443
    # within the cloudflared namespace scope and rely on the above rules to keep traffic local.
    # Since K8s NetworkPolicy cannot match by destination hostname, use a broad rule for public 443.
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 7844       # cloudflared quic tunnel port
        - protocol: UDP
          port: 7844
{{- end }}
```

Note: this is the pragmatic compromise for Flannel / kube-router — we cannot restrict to Cloudflare's IPs without a CIDR list update job. If Cloudflare publishes a CIDR endpoint for the tunnel protocol, the `ipBlock` could be narrowed later.

- [ ] **Step 2: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set cloudflare.enabled=true \
  --set cloudflare.tunnelToken=x \
  --set monitoring.grafana.adminPassword=y \
  --set monitoring.postgresExporter.password=z \
  2>&1 | grep -A 10 "name: cloudflared-egress"
```

Expected: NetworkPolicy renders.

- [ ] **Step 3: Commit**

```bash
git add charts/lolday/templates/netpol-cloudflared.yaml
git commit -m "feat(charts): NetworkPolicy restricting cloudflared egress"
```

---

### Task 16: Update `values.yaml` — `cloudflare.enabled=true` + new host

**Goal:** Switch `frontend.host` to `lolday.connlabai.com` so Traefik routes the Cloudflare-tunneled traffic.

**Files:**

- Modify: `charts/lolday/values.yaml`

- [ ] **Step 1: Edit `values.yaml`**

Change:

```yaml
cloudflare:
  enabled: false
  tunnelToken: ""
  replicas: 2
```

to:

```yaml
cloudflare:
  enabled: true
  tunnelToken: "" # --set at deploy time via $CF_TUNNEL_TOKEN
  replicas: 2
  image: cloudflare/cloudflared:2026.3.0 # pin explicit tag (no :latest)
```

And change the frontend block:

```yaml
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:phase5
  host: lolday.islab.local
```

to:

```yaml
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:phase5
  host: lolday.connlabai.com
```

- [ ] **Step 2: Pin cloudflared image in the existing Deployment template**

In `charts/lolday/templates/cloudflared.yaml`, change:

```yaml
image: cloudflare/cloudflared:latest
```

to:

```yaml
image:
  { { .Values.cloudflare.image | default "cloudflare/cloudflared:2026.3.0" } }
```

- [ ] **Step 3: Add Host header config in Tunnel dashboard (reminder, not code)**

This step is performed in Cloudflare dashboard (Task 17). Keep the YAML as-is; only Cloudflare-side config adds the Host header override.

- [ ] **Step 4: Dry-run render**

```bash
helm template lolday charts/lolday \
  --set cloudflare.tunnelToken=x \
  --set monitoring.grafana.adminPassword=y \
  --set monitoring.postgresExporter.password=z \
  2>&1 | grep -B 1 -A 1 "Host(\`" | head -10
```

Expected: Traefik IngressRoute uses ``Host(`lolday.connlabai.com`)``.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/values.yaml charts/lolday/templates/cloudflared.yaml
git commit -m "feat(charts): enable cloudflare tunnel; switch host to lolday.connlabai.com"
```

---

### Task 17: Add Public Hostname to Cloudflare Tunnel (manual)

**Goal:** Make the Tunnel's DNS target point at in-cluster Traefik.

- [ ] **Step 1: Open Tunnel configuration**

Cloudflare dashboard → Zero Trust → Networks → Tunnels → `lolday-server30` → Configure → Public Hostname tab → Add a public hostname.

- [ ] **Step 2: Fill the fields**

- Subdomain: `lolday`
- Domain: `connlabai.com`
- Path: (leave empty)
- Service Type: `HTTP`
- URL: `traefik.kube-system.svc.cluster.local:80`

Expand "Additional application settings" → HTTP Settings → set `HTTP Host Header` to `lolday.connlabai.com`.

Save.

- [ ] **Step 3: Verify DNS**

```bash
dig +short lolday.connlabai.com
```

Expected: a CNAME to `<uuid>.cfargotunnel.com` OR an A record that Cloudflare set (depends on proxy mode). Either way, not empty.

---

### Task 18: Deploy sub-phase 6-3 + end-to-end verification

**Goal:** Roll out cloudflared + new host, confirm end-to-end flow through Cloudflare Access.

- [ ] **Step 1: Run pre-deploy check**

```bash
source ~/.lolday-secrets.env
bash scripts/phase6-pre-deploy-check.sh
```

Expected: all pass, including `CF_ENABLED=true` branch.

- [ ] **Step 2: Deploy**

```bash
source ~/.lolday-secrets.env
bash scripts/deploy.sh
```

Expected: `Deploy complete`.

- [ ] **Step 3: Verify cloudflared pods**

```bash
kubectl -n lolday get pod -l app.kubernetes.io/component=cloudflared
```

Expected: 2 pods, both `1/1 Running`. Then:

```bash
kubectl -n lolday logs -l app.kubernetes.io/component=cloudflared --tail=20 | grep -iE "Registered tunnel connection|Connected"
```

Expected: both replicas log "Registered tunnel connection connIndex=0" (one per pod). Two connection lines total is acceptable.

- [ ] **Step 4: Anonymous request blocked**

```bash
curl -sI https://lolday.connlabai.com | head -5
```

Expected: HTTP 302 with `location:` pointing at `https://<team>.cloudflareaccess.com/...`. **Not** a 200 from lolday, not a 401 from FastAPI.

- [ ] **Step 5: Direct login bypass blocked**

```bash
curl -siX POST https://lolday.connlabai.com/api/v1/auth/login \
  -d 'username=x&password=y' \
  -H 'Content-Type: application/x-www-form-urlencoded' | head -5
```

Expected: 302 to Cloudflare Access. **Not** a JSON 401 from FastAPI.

- [ ] **Step 6: NTUST SSO happy-path**

In a browser:

1. Open `https://lolday.connlabai.com`
2. Sign in with a Google account ending in `@mail.ntust.edu.tw`
3. See lolday login page
4. Log in with platform credentials from `~/.lolday-secrets.env`
5. Verify the Detectors page renders with data

Record pass/fail.

- [ ] **Step 7: Non-NTUST Google account denied**

In a private browser session (or with a logout + alternate Google account):

1. Open `https://lolday.connlabai.com`
2. Sign in with a Google account that does NOT end in `@mail.ntust.edu.tw`
3. Expect Cloudflare Access denial page: "You do not have permission to access this application."

Record pass/fail.

- [ ] **Step 8: NetworkPolicy enforcement spot-check**

```bash
kubectl -n lolday exec -it deploy/cloudflared -- \
  wget -O- --timeout=5 http://postgresql.lolday.svc:5432 2>&1 | head -5
```

Expected: timeout or connection refused within 5 s (the NetworkPolicy blocks this).

```bash
kubectl -n lolday exec -it deploy/cloudflared -- \
  wget -O- --timeout=5 http://traefik.kube-system.svc:80 2>&1 | head -5
```

Expected: HTTP response from Traefik (probably a 404 because no Host header matches; that's fine — it proves connectivity).

- [ ] **Step 9: Commit any adjustments discovered during deploy**

```bash
git status
# If deploy.sh or any template was adjusted, commit here.
```

---

### Task 19: Update Playwright config + re-run Phase 5 E2E

**Goal:** Keep Phase 5 Playwright regression working after the host change. The Phase 5 config already uses an env-driven `BASE_URL` and a `--host-resolver-rules` mapping to `127.0.0.1`; only the hardcoded `DEPLOYED_HOST` constant needs updating.

**Files:**

- Modify: `frontend/playwright.config.ts`

- [ ] **Step 1: Edit `frontend/playwright.config.ts`**

Change line 8:

```ts
const DEPLOYED_HOST = "lolday.islab.local";
```

to:

```ts
const DEPLOYED_HOST = "lolday.connlabai.com";
```

No other changes are needed. The `BASE_URL` env variable and `host-resolver-rules=MAP ${DEPLOYED_HOST} 127.0.0.1` logic stays as-is — tests run with a local `kubectl port-forward svc/traefik -n kube-system 80:80` and Chromium's resolver rule routes the new hostname to that port-forward, bypassing Cloudflare Access entirely.

- [ ] **Step 2: Set up a local port-forward to Traefik**

```bash
# Traefik's HTTP entrypoint is typically port 80 inside the container; the Service exposes it on 80.
sudo kubectl -n kube-system port-forward svc/traefik 80:80 &
PF_PID=$!
# If binding to privileged port 80 isn't possible without sudo, use a different local port
# AND prefix E2E_BASE_URL with the same port, e.g.:
#   kubectl -n kube-system port-forward svc/traefik 8080:80 &
#   export E2E_BASE_URL="http://lolday.connlabai.com:8080"
# Then include the port in the Chromium host-resolver-rule by changing line 10 to:
#   [`--host-resolver-rules=MAP ${DEPLOYED_HOST}:8080 127.0.0.1:8080`]
```

Prefer the non-sudo path (port 8080) — server30's `bolin8017` does not have persistent sudo. Update `playwright.config.ts` line 10 accordingly if you pick port 8080.

- [ ] **Step 3: Run Playwright E2E**

```bash
cd frontend
export E2E_BASE_URL="http://lolday.connlabai.com:8080"   # if using port 8080
pnpm exec playwright test
```

Expected: all 5 specs pass.

- [ ] **Step 4: Stop the port-forward**

```bash
kill $PF_PID
```

- [ ] **Step 5: Commit**

```bash
git add frontend/playwright.config.ts
git commit -m "test(frontend): playwright DEPLOYED_HOST → lolday.connlabai.com"
```

---

### Task 20: Chaos tests (manual, recorded)

**Goal:** Confirm cloudflared HA behaviour, disk-full alert, and internal-access fallback.

- [ ] **Step 1: Kill one cloudflared replica**

```bash
POD=$(kubectl -n lolday get pod -l app.kubernetes.io/component=cloudflared -o jsonpath='{.items[0].metadata.name}')
kubectl -n lolday delete pod "$POD"
# From external network, immediately retry:
curl -sI https://lolday.connlabai.com | head -1
```

Expected: external access uninterrupted (the second replica carries traffic). New pod comes back `Running` within ~15 s.

- [ ] **Step 2: Kill both replicas**

```bash
kubectl -n lolday delete pod -l app.kubernetes.io/component=cloudflared
```

Expected: `https://lolday.connlabai.com` briefly returns 502 / timeout; within ~30 s both replicas are Running and access restored.

While down: SSH to server30 + `kubectl port-forward svc/frontend 8080:80` MUST still let the admin reach the UI locally on http://localhost:8080. Confirm.

- [ ] **Step 3: Disk full alert simulation**

```bash
# Allocate a 5-GiB sparse file on the monitoring disk to push utilization > 85%
# (monitor current free first; if /mnt/ssd500g has >150 Gi free this won't trigger the alert).
df -h /mnt/ssd500g
# Skip if plenty of room. If the test is needed for demo purposes, allocate
# enough to push above 85%:
# fallocate -l 200G /mnt/ssd500g/lolday-monitoring/_chaos_fill.bin
```

Expected: `NodeDiskAlmostFull` alert fires in Alertmanager UI within `for: 10m`. Remove the file:

```bash
rm /mnt/ssd500g/lolday-monitoring/_chaos_fill.bin
```

- [ ] **Step 4: Record results**

Append chaos results to the checklist being drafted in Task 21.

No commit for this task.

---

### Task 21: Write `docs/phase6-e2e-checklist.md`

**Goal:** Capture the single source of truth for "Phase 6 is done".

**Files:**

- Create: `docs/phase6-e2e-checklist.md`

- [ ] **Step 1: Draft the checklist**

```markdown
# Phase 6 E2E Checklist

> Parallel to `phase4-e2e-checklist.md`. Run top-to-bottom after each sub-phase deploy.

## Prerequisites

- [ ] `/mnt/ssd500g/lolday-monitoring` exists, writable, ≥ 60 Gi free
- [ ] `~/.lolday-secrets.env` sources cleanly, contains `GRAFANA_ADMIN_PASSWORD`, `PG_EXPORTER_PASSWORD`, `CF_ENABLED`, `CF_TUNNEL_TOKEN`
- [ ] `connlabai.com` is in Cloudflare DNS
- [ ] Cloudflare Tunnel `lolday-server30` exists
- [ ] Access Application `lolday` with the NTUST policy exists

## Sub-phase 6-1 — Monitoring stack

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [ ] All pods in `monitoring` namespace Running
- [ ] Grafana reachable via `kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-grafana 3000:80`
- [ ] Grafana login works with `admin` / `$GRAFANA_ADMIN_PASSWORD`
- [ ] Dashboard "Kubernetes / Compute Resources / Cluster" has data
- [ ] Dashboard "NVIDIA DCGM Exporter Dashboard" shows both GPUs
- [ ] Dashboard "Traefik 3" shows request counters
- [ ] Dashboard "PostgreSQL Database" shows connections
- [ ] LogQL `{namespace="lolday"}` returns results
- [ ] `curl /api/v1/targets` on Prometheus shows ≥ 10 up jobs
- [ ] Alertmanager UI (port-forward 9093) lists 4 inactive baseline alerts
- [ ] Phase 4 curl E2E passes
- [ ] Phase 5 Playwright E2E passes

## Sub-phase 6-2 — Access policy

- [ ] Cloudflare → Zero Trust → Applications shows `lolday` with NTUST policy
- [ ] `cloudflared access login https://lolday.connlabai.com` issues a token for an NTUST Google account
- [ ] Same command with a non-NTUST account shows Access Denied

## Sub-phase 6-3 — Tunnel + Access live

Run: `bash scripts/phase6-pre-deploy-check.sh && bash scripts/deploy.sh`

- [ ] 2 `cloudflared` pods Running in `lolday` namespace
- [ ] Both logs print "Registered tunnel connection"
- [ ] Anonymous `curl -I https://lolday.connlabai.com` returns 302 to cloudflareaccess.com
- [ ] Non-NTUST Google login → Access Denied screen
- [ ] NTUST Google login → lolday login page → platform credentials → Detectors page
- [ ] `cloudflared` pod cannot reach postgresql.lolday.svc:5432 (NetworkPolicy)
- [ ] Phase 4 curl E2E passes (via in-cluster port-forward)
- [ ] Phase 5 Playwright E2E passes (via Traefik LB + host-resolver-rules)

## Chaos (record findings)

- [ ] Delete 1 cloudflared pod → external access continues
- [ ] Delete both cloudflared pods → external access restores within 30 s; internal port-forward access works during outage
- [ ] Optional: fill `/mnt/ssd500g/lolday-monitoring` → `NodeDiskAlmostFull` alert fires

## Security

- [ ] Anonymous request blocked at edge (302 to cloudflareaccess)
- [ ] Direct /api/v1/auth/login bypass blocked at edge
- [ ] With valid cf-access-token, `/api/v1/health` returns 200 through Cloudflare

## Sign-off

- [ ] Date: **\_**
- [ ] Verifier: **\_**
```

- [ ] **Step 2: Commit**

```bash
git add docs/phase6-e2e-checklist.md
git commit -m "docs: phase 6 E2E checklist"
```

---

## Final task: Merge + record

### Task 22: Final regression + squash-merge

**Goal:** Run everything one last time, then merge `phase6-impl` (or current branch) to `main` with the squash-merge pattern used for Phase 3 / 4 / 5.

- [ ] **Step 1: Final regression**

Run both curl and Playwright E2E one last time:

```bash
source ~/.lolday-secrets.env
kubectl -n lolday port-forward svc/backend 8000:8000 &
sleep 2
curl -sX POST http://localhost:8000/api/v1/auth/login \
  -d "username=$ADMIN_EMAIL&password=$ADMIN_PASSWORD" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq .access_token
kill %1 || true

cd frontend && pnpm exec playwright test && cd ..
```

Expected: both pass.

- [ ] **Step 2: Work through `docs/phase6-e2e-checklist.md`**

Check every box. File any unchecked items as follow-up issues.

- [ ] **Step 3: Squash-merge**

```bash
git checkout main
git merge --squash phase6-impl      # or current branch name
git commit -m "$(cat <<'EOF'
feat: phase 6 — operations (monitoring + Cloudflare Tunnel + Zero Trust)

- Monitoring stack: kube-prometheus-stack, Loki, Promtail, DCGM
- Backend /metrics via prometheus-fastapi-instrumentator
- postgres-exporter + dedicated monitoring-local StorageClass on NVMe
- Cloudflare Tunnel (cloudflared x 2 HA) + Zero Trust Access with
  Google SSO for @mail.ntust.edu.tw
- NetworkPolicy limiting cloudflared egress
- Host migration: lolday.islab.local → lolday.connlabai.com
- 4 baseline alert rules, 3 provisioned Grafana dashboards
- docs/phase6-e2e-checklist.md

Regression: Phase 4 curl E2E + Phase 5 Playwright E2E both pass.

Deferred to Phase 7+: backup (pg_dump, etcd, MLflow → R2),
Resend notifications, Volcano, NFS CSI, Trivy Operator.
EOF
)"
git push origin main
```

- [ ] **Step 4: Update the auto-memory note**

Update `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_lolday_overview.md`:

- Mark Phase 6 as done with date and commit hash
- Add Phase 6 specific notes (what's in monitoring, Tunnel config, new external URL)
- Bump "current status" line

This is the closing step. The project is now functionally complete per the original design spec's minimum viable platform; remaining items (backup, notifications, Volcano, etc.) are tracked as Phase 7+.

---

## Notes for implementers

- If Harbor metrics aren't enabled in its subchart (Task 9, Step 3), extend `harbor.metrics.enabled: true` in `values.yaml` as part of the same task commit.
- If the Traefik Service lacks a `metrics` named port, the quick fix (Task 13, Step 1) is safe to apply manually; the proper fix is to edit `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml` with a `HelmChartConfig` — document but do not apply unless the Service patch fails.
- Grafana sidecar can take up to 60 s after Grafana boot to pick up dashboards in `grafana-dashboards-phase6` ConfigMap. If dashboards don't appear, restart the Grafana pod: `kubectl -n monitoring rollout restart deploy/kps-kube-prometheus-stack-grafana`.
- Cloudflare Tunnel Public Hostname (Task 17) can be configured via API instead of dashboard; both end up with the same state. Dashboard is easier to review and audit.
- If `cloudflared` pods enter CrashLoopBackOff, confirm `CF_TUNNEL_TOKEN` matches exactly what Cloudflare dashboard shows for the tunnel. Token rotation requires redeploy.
- `prometheus-fastapi-instrumentator` version 7 aligns with FastAPI 0.115+ used in this repo; if the dependency resolver balks, pin `~=7.0` explicitly.
