# Phase 1: Infrastructure Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the complete infrastructure foundation for the lolday platform — cluster networking, GPU operator, batch scheduler, container registry, NFS storage, and Cloudflare tunnel.

**Architecture:** K3s cluster with Cilium CNI, NVIDIA GPU Operator for GPU management, Volcano for batch scheduling, Harbor for container registry, NFS CSI for dataset access, and Cloudflare Tunnel for secure external access. Cluster-level components are installed via a setup script; application-level components are managed by a Helm umbrella chart.

**Tech Stack:** K3s 1.34, Cilium, NVIDIA GPU Operator, Volcano, Harbor, NFS CSI Driver, Cloudflare Tunnel, Helm 3

**Server:** server30 (Ubuntu 24.04, 2x RTX 2080 Ti 11GB, IP 140.118.155.30)

---

## File Structure

```
lolday/
├── charts/
│   └── lolday/
│       ├── Chart.yaml                    # Umbrella Helm chart
│       ├── values.yaml                   # Default configuration values
│       └── templates/
│           ├── _helpers.tpl              # Template helper functions
│           └── namespace.yaml            # Namespace definition
├── scripts/
│   ├── setup-cluster.sh                  # Install cluster-level components (Cilium, GPU Operator, Volcano)
│   ├── deploy.sh                         # Deploy lolday umbrella chart
│   └── teardown.sh                       # Uninstall everything
├── .gitignore
└── README.md
```

**Design decisions:**

- Cluster-level components (Cilium, GPU Operator, Volcano) are installed separately via `setup-cluster.sh` because they have their own lifecycle and manage cluster-wide resources.
- Application-level dependencies (Harbor, NFS CSI, Cloudflared, and future app services) are managed as Helm sub-chart dependencies in the umbrella chart.
- This split means `setup-cluster.sh` runs once per cluster, while `helm upgrade lolday` handles app-level changes.

---

### Task 1: Project Structure and .gitignore

**Files:**

- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Create .gitignore**

```gitignore
# Helm
charts/lolday/charts/
charts/lolday/*.tgz

# Python (future phases)
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/

# Node (future phases)
node_modules/
.next/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Secrets — NEVER commit
*.env
*.env.*
!*.env.example
**/secrets/

# Superpowers brainstorm sessions
.superpowers/
```

- [ ] **Step 2: Create README.md**

````markdown
# Lolday

Internal ML platform for ISLab malware detector management.

## Prerequisites

- K3s (installed by admin with `--flannel-backend=none --disable-network-policy`)
- kubectl, Helm, Cilium CLI, Trivy, Cloudflared, k9s
- NVIDIA drivers on GPU nodes

## Quick Start

```bash
# 1. Setup cluster-level components (once per cluster)
./scripts/setup-cluster.sh

# 2. Deploy the platform
./scripts/deploy.sh

# 3. Teardown (removes everything)
./scripts/teardown.sh
```
````

## Documentation

- [Design Spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1: Infrastructure](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)

````

- [ ] **Step 3: Commit**

```bash
git add .gitignore README.md
git commit -m "chore: add .gitignore and README"
````

---

### Task 2: Install Cilium and Verify Cluster

**Files:** None (cluster operation)

**Prerequisites:** K3s is running with `--flannel-backend=none --disable-network-policy`

- [ ] **Step 1: Install Cilium**

```bash
cilium install
```

Expected: Cilium pods start deploying. This takes 1-2 minutes.

- [ ] **Step 2: Wait for Cilium to be ready**

```bash
cilium status --wait
```

Expected output includes:

```
    /¯¯\
 /¯¯\__/¯¯\    Cilium:          OK
 \__/¯¯\__/    Operator:        OK
 /¯¯\__/¯¯\    ...
```

- [ ] **Step 3: Verify node is Ready**

```bash
kubectl get nodes
```

Expected:

```
NAME       STATUS   ROLES           AGE   VERSION
server30   Ready    control-plane   ...   v1.34.5+k3s1
```

Status must be `Ready` (was `NotReady` before Cilium).

- [ ] **Step 4: Run Cilium connectivity test**

```bash
cilium connectivity test --single-node
```

Expected: All tests pass. This validates NetworkPolicy enforcement works.

- [ ] **Step 5: Verify GPU nodes are visible**

```bash
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.capacity}{"\n"}{end}'
```

Expected: Node `server30` is listed. GPU resources will appear after GPU Operator is installed.

---

### Task 3: Install NVIDIA GPU Operator

**Files:** None (cluster operation)

**Prerequisites:** NVIDIA drivers installed on host, Cilium running

- [ ] **Step 1: Add NVIDIA Helm repo**

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
```

- [ ] **Step 2: Install GPU Operator**

The host already has NVIDIA drivers (nvidia-smi works), so we disable the driver container and let the operator manage the device plugin and toolkit only.

```bash
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m
```

Note: `driver.enabled=false` because the host already has NVIDIA drivers installed. `dcgmExporter.enabled=true` installs DCGM Exporter for GPU monitoring (used by Prometheus in Phase 7).

- [ ] **Step 3: Wait for GPU Operator pods to be ready**

```bash
kubectl -n gpu-operator get pods -w
```

Wait until all pods show `Running` or `Completed`. Key pods:

- `nvidia-device-plugin-daemonset-*` — must be Running
- `nvidia-container-toolkit-daemonset-*` — must be Running
- `gpu-operator-*` — must be Running
- `dcgm-exporter-*` — must be Running

- [ ] **Step 4: Verify GPUs are registered as K8s resources**

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | python3 -m json.tool | grep nvidia
```

Expected:

```
"nvidia.com/gpu": "2"
```

This confirms K8s sees 2x RTX 2080 Ti as schedulable resources.

- [ ] **Step 5: Test GPU access with a Pod**

```bash
kubectl run gpu-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.6.3-base-ubuntu24.04 \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi
```

Expected: `nvidia-smi` output showing one RTX 2080 Ti inside the container. Pod auto-deletes after completion.

---

### Task 4: Install Volcano Batch Scheduler

**Files:** None (cluster operation)

- [ ] **Step 1: Add Volcano Helm repo**

```bash
helm repo add volcano-sh https://volcano-sh.github.io/charts
helm repo update
```

- [ ] **Step 2: Install Volcano**

```bash
helm install volcano volcano-sh/volcano \
  -n volcano-system --create-namespace \
  --wait --timeout 3m
```

- [ ] **Step 3: Verify Volcano components**

```bash
kubectl -n volcano-system get pods
```

Expected: All pods Running:

- `volcano-admission-*`
- `volcano-controllers-manager-*`
- `volcano-scheduler-*`

- [ ] **Step 4: Test Volcano with a GPU job**

Create a test job to verify Volcano can schedule GPU workloads:

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: batch.volcano.sh/v1alpha1
kind: Job
metadata:
  name: test-volcano-gpu
spec:
  minAvailable: 1
  schedulerName: volcano
  tasks:
    - replicas: 1
      name: gpu-task
      template:
        spec:
          containers:
            - name: cuda
              image: nvidia/cuda:12.6.3-base-ubuntu24.04
              command: ["nvidia-smi"]
              resources:
                limits:
                  nvidia.com/gpu: 1
          restartPolicy: Never
EOF
```

- [ ] **Step 5: Verify test job completed**

```bash
kubectl get vcjob test-volcano-gpu -o jsonpath='{.status.state.phase}'
```

Expected: `Completed`

```bash
kubectl logs -l volcano.sh/job-name=test-volcano-gpu
```

Expected: `nvidia-smi` output showing one GPU.

- [ ] **Step 6: Clean up test job**

```bash
kubectl delete vcjob test-volcano-gpu
```

---

### Task 5: Create Helm Umbrella Chart Skeleton

**Files:**

- Create: `charts/lolday/Chart.yaml`
- Create: `charts/lolday/values.yaml`
- Create: `charts/lolday/templates/_helpers.tpl`
- Create: `charts/lolday/templates/namespace.yaml`

- [ ] **Step 1: Create Chart.yaml with dependencies**

```yaml
apiVersion: v2
name: lolday
description: ISLab Malware Detector Management Platform
type: application
version: 0.1.0
appVersion: "0.1.0"

dependencies:
  # Container Registry
  - name: harbor
    version: "~1.16"
    repository: "https://helm.goharbor.io"
    condition: harbor.enabled

  # NFS storage for datasets
  - name: nfs-subdir-external-provisioner
    version: "~4.0"
    repository: "https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner"
    condition: nfs.enabled
```

- [ ] **Step 2: Create values.yaml**

```yaml
# =============================================================================
# Lolday Platform Configuration
# =============================================================================

# -- Global settings
global:
  namespace: lolday

# =============================================================================
# Harbor — Container Registry
# =============================================================================
harbor:
  enabled: true
  expose:
    type: clusterIP
  externalURL: https://harbor.lolday.local
  persistence:
    enabled: true
    persistentVolumeClaim:
      registry:
        size: 50Gi
      database:
        size: 5Gi
  # Disable internal TLS (Traefik handles TLS termination)
  internalTLS:
    enabled: false
  harborAdminPassword: "" # Set via --set at install time, NEVER commit

# =============================================================================
# NFS — Dataset Storage
# =============================================================================
nfs:
  enabled: true

nfs-subdir-external-provisioner:
  nfs:
    server: "" # Set via --set, e.g., 140.118.155.x
    path: "" # Set via --set, e.g., /mnt/datasets
    mountOptions:
      - nfsvers=4
      - ro
      - noexec
      - nosuid
      - nodev
  storageClass:
    name: nfs-datasets
    reclaimPolicy: Retain
    accessModes: ReadOnlyMany

# =============================================================================
# Cloudflare Tunnel (deployed in later task)
# =============================================================================
cloudflare:
  enabled: false # Enable after domain is purchased
  tunnelToken: "" # Set via --set, NEVER commit
  replicas: 2
```

- [ ] **Step 3: Create templates/\_helpers.tpl**

```yaml
{{/*
Common labels
*/}}
{{- define "lolday.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "lolday.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
```

- [ ] **Step 4: Create templates/namespace.yaml**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: { { .Values.global.namespace } }
  labels: { { - include "lolday.labels" . | nindent 4 } }
```

- [ ] **Step 5: Build dependencies**

```bash
cd charts/lolday
helm dependency build
cd ../..
```

Expected: `charts/lolday/charts/` directory created with `.tgz` files for harbor and nfs-subdir-external-provisioner.

- [ ] **Step 6: Lint the chart**

```bash
helm lint charts/lolday
```

Expected: `0 chart(s) failed` — no errors.

- [ ] **Step 7: Commit**

```bash
git add charts/
git commit -m "feat: add Helm umbrella chart with Harbor and NFS dependencies"
```

---

### Task 6: Deploy Harbor Container Registry

**Files:** None (Helm operation using chart from Task 5)

- [ ] **Step 1: Generate a Harbor admin password**

```bash
HARBOR_PASS=$(openssl rand -base64 16)
echo "Harbor admin password: $HARBOR_PASS"
echo "Save this password securely!"
```

- [ ] **Step 2: Deploy the umbrella chart (Harbor + NFS)**

Replace `<NFS_SERVER_IP>` and `<NFS_PATH>` with your actual NFS server details.

```bash
helm install lolday charts/lolday \
  -n lolday --create-namespace \
  --set harbor.harborAdminPassword="$HARBOR_PASS" \
  --set nfs-subdir-external-provisioner.nfs.server=<NFS_SERVER_IP> \
  --set nfs-subdir-external-provisioner.nfs.path=<NFS_PATH> \
  --wait --timeout 10m
```

Note: If NFS is not yet available, disable it for now:

```bash
helm install lolday charts/lolday \
  -n lolday --create-namespace \
  --set harbor.harborAdminPassword="$HARBOR_PASS" \
  --set nfs.enabled=false \
  --wait --timeout 10m
```

- [ ] **Step 3: Verify Harbor pods are running**

```bash
kubectl -n lolday get pods -l app=harbor
```

Expected: All Harbor pods Running:

- `harbor-core-*`
- `harbor-database-*`
- `harbor-jobservice-*`
- `harbor-portal-*`
- `harbor-redis-*`
- `harbor-registry-*`
- `harbor-trivy-*`

- [ ] **Step 4: Port-forward and verify Harbor UI**

```bash
kubectl -n lolday port-forward svc/harbor-portal 8080:80 &
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080
```

Expected: `200`

Kill the port-forward after verifying:

```bash
kill %1
```

- [ ] **Step 5: Create a lolday project in Harbor via API**

```bash
kubectl -n lolday port-forward svc/harbor-core 8081:80 &
sleep 2

curl -s -X POST http://localhost:8081/api/v2.0/projects \
  -H "Content-Type: application/json" \
  -u "admin:$HARBOR_PASS" \
  -d '{"project_name":"lolday","public":false}'

kill %1
```

Expected: HTTP 201 Created. This creates a private `lolday` project in Harbor where detector images will be stored.

---

### Task 7: Verify NFS CSI Driver (if NFS available)

**Files:** None (cluster operation)

**Prerequisites:** NFS server is accessible from server30

- [ ] **Step 1: Verify NFS provisioner is running**

```bash
kubectl -n lolday get pods -l app=nfs-subdir-external-provisioner
```

Expected: Pod in `Running` state.

- [ ] **Step 2: Verify StorageClass exists**

```bash
kubectl get storageclass nfs-datasets
```

Expected: StorageClass `nfs-datasets` with provisioner `cluster.local/lolday-nfs-subdir-external-provisioner`.

- [ ] **Step 3: Test NFS mount with a temporary Pod**

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: Pod
metadata:
  name: nfs-test
  namespace: lolday
spec:
  containers:
    - name: test
      image: busybox:1.36
      command: ["sh", "-c", "ls /data && echo 'NFS mount OK' && sleep 5"]
      volumeMounts:
        - name: dataset
          mountPath: /data
          readOnly: true
  volumes:
    - name: dataset
      nfs:
        server: <NFS_SERVER_IP>
        path: <NFS_PATH>
        readOnly: true
  restartPolicy: Never
EOF
```

- [ ] **Step 4: Verify NFS mount works**

```bash
kubectl -n lolday wait --for=condition=Ready pod/nfs-test --timeout=30s
kubectl -n lolday logs nfs-test
```

Expected: File listing from NFS and `NFS mount OK`.

- [ ] **Step 5: Clean up test pod**

```bash
kubectl -n lolday delete pod nfs-test
```

---

### Task 8: Create Deployment Scripts

**Files:**

- Create: `scripts/setup-cluster.sh`
- Create: `scripts/deploy.sh`
- Create: `scripts/teardown.sh`

- [ ] **Step 1: Create setup-cluster.sh**

This script installs cluster-level components that live outside the Helm umbrella chart.

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Lolday Cluster Setup ==="
echo ""

# -------------------------------------------------------
# 1. Cilium CNI
# -------------------------------------------------------
echo "[1/3] Installing Cilium..."
if cilium status --wait 2>/dev/null | grep -q "OK"; then
  echo "  Cilium already installed and running. Skipping."
else
  cilium install
  cilium status --wait
fi
echo "  ✓ Cilium ready"
echo ""

# -------------------------------------------------------
# 2. NVIDIA GPU Operator
# -------------------------------------------------------
echo "[2/3] Installing NVIDIA GPU Operator..."
if helm status gpu-operator -n gpu-operator &>/dev/null; then
  echo "  GPU Operator already installed. Skipping."
else
  helm repo add nvidia https://helm.ngc.nvidia.com/nvidia --force-update
  helm repo update
  helm install gpu-operator nvidia/gpu-operator \
    -n gpu-operator --create-namespace \
    --set driver.enabled=false \
    --set toolkit.enabled=true \
    --set devicePlugin.enabled=true \
    --set dcgmExporter.enabled=true \
    --wait --timeout 5m
fi
echo "  ✓ GPU Operator ready"
echo ""

# -------------------------------------------------------
# 3. Volcano Batch Scheduler
# -------------------------------------------------------
echo "[3/3] Installing Volcano..."
if helm status volcano -n volcano-system &>/dev/null; then
  echo "  Volcano already installed. Skipping."
else
  helm repo add volcano-sh https://volcano-sh.github.io/charts --force-update
  helm repo update
  helm install volcano volcano-sh/volcano \
    -n volcano-system --create-namespace \
    --wait --timeout 3m
fi
echo "  ✓ Volcano ready"
echo ""

# -------------------------------------------------------
# Verification
# -------------------------------------------------------
echo "=== Verification ==="

echo -n "Nodes: "
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}({.status.conditions[-1].type}={.status.conditions[-1].status}) {end}'
echo ""

GPU_COUNT=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "0")
echo "GPUs available: $GPU_COUNT"

echo ""
echo "=== Cluster setup complete ==="
```

- [ ] **Step 2: Create deploy.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$SCRIPT_DIR/../charts/lolday"

echo "=== Lolday Platform Deploy ==="
echo ""

# Check required arguments
if [ -z "${HARBOR_PASS:-}" ]; then
  echo "Error: HARBOR_PASS environment variable is required."
  echo "Usage: HARBOR_PASS=<password> ./scripts/deploy.sh"
  exit 1
fi

# Build Helm dependencies
echo "[1/2] Building Helm dependencies..."
helm dependency build "$CHART_DIR"
echo "  ✓ Dependencies ready"
echo ""

# Deploy or upgrade
echo "[2/2] Deploying lolday..."
HELM_CMD="upgrade --install"

helm $HELM_CMD lolday "$CHART_DIR" \
  -n lolday --create-namespace \
  --set harbor.harborAdminPassword="$HARBOR_PASS" \
  --set nfs-subdir-external-provisioner.nfs.server="${NFS_SERVER:-}" \
  --set nfs-subdir-external-provisioner.nfs.path="${NFS_PATH:-}" \
  --set nfs.enabled="${NFS_ENABLED:-false}" \
  --set cloudflare.enabled="${CF_ENABLED:-false}" \
  --set cloudflare.tunnelToken="${CF_TUNNEL_TOKEN:-}" \
  --wait --timeout 10m

echo "  ✓ Lolday deployed"
echo ""

echo "=== Deploy complete ==="
kubectl -n lolday get pods
```

- [ ] **Step 3: Create teardown.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Lolday Teardown ==="
echo ""
echo "WARNING: This will remove all lolday components."
echo "Data in Persistent Volumes will be retained (Retain policy)."
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "[1/4] Removing lolday Helm release..."
helm uninstall lolday -n lolday 2>/dev/null || echo "  Not installed, skipping."

echo "[2/4] Removing Volcano..."
helm uninstall volcano -n volcano-system 2>/dev/null || echo "  Not installed, skipping."

echo "[3/4] Removing GPU Operator..."
helm uninstall gpu-operator -n gpu-operator 2>/dev/null || echo "  Not installed, skipping."

echo "[4/4] Removing Cilium..."
cilium uninstall 2>/dev/null || echo "  Not installed, skipping."

echo ""
echo "=== Teardown complete ==="
echo "Note: Namespaces and PVs may still exist. Remove manually if needed."
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x scripts/setup-cluster.sh scripts/deploy.sh scripts/teardown.sh
```

- [ ] **Step 5: Commit**

```bash
git add scripts/
git commit -m "feat: add cluster setup, deploy, and teardown scripts"
```

---

### Task 9: Cloudflare Tunnel Configuration

**Files:**

- Create: `charts/lolday/templates/cloudflared-deployment.yaml`
- Create: `charts/lolday/templates/cloudflared-secret.yaml`
- Modify: `charts/lolday/values.yaml`

**Prerequisites:** User has purchased a domain on Cloudflare and created a tunnel via the Cloudflare dashboard.

**Note:** This task creates the K8s manifests. The actual Cloudflare account setup (buy domain, create tunnel, get token) is a manual prerequisite documented in the steps below.

- [ ] **Step 1: Manual — Cloudflare account setup**

These steps are done in the Cloudflare dashboard (not automated):

1. Buy a domain at [Cloudflare Registrar](https://dash.cloudflare.com/domains)
2. Go to **Zero Trust → Networks → Tunnels**
3. Click **Create a tunnel** → select **Cloudflared**
4. Name it `lolday`
5. Copy the tunnel token (starts with `eyJ...`)
6. Add a **Public Hostname**:
   - Subdomain: (leave blank or `app`)
   - Domain: your purchased domain
   - Service: `http://traefik.kube-system.svc.cluster.local:80`
7. Go to **Zero Trust → Access → Applications**
8. Create an application with policy to restrict by IP range or email domain

Save the tunnel token — it will be passed as `CF_TUNNEL_TOKEN` at deploy time.

- [ ] **Step 2: Create cloudflared-secret.yaml**

```yaml
{{- if .Values.cloudflare.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: cloudflared-tunnel-token
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
type: Opaque
stringData:
  tunnel-token: {{ .Values.cloudflare.tunnelToken | quote }}
{{- end }}
```

- [ ] **Step 3: Create cloudflared-deployment.yaml**

```yaml
{{- if .Values.cloudflare.enabled }}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: cloudflared
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.cloudflare.replicas }}
  selector:
    matchLabels:
      app.kubernetes.io/component: cloudflared
  template:
    metadata:
      labels:
        app.kubernetes.io/component: cloudflared
    spec:
      containers:
        - name: cloudflared
          image: cloudflare/cloudflared:2026.3.0
          args:
            - tunnel
            - --no-autoupdate
            - run
            - --token
            - $(TUNNEL_TOKEN)
          env:
            - name: TUNNEL_TOKEN
              valueFrom:
                secretKeyRef:
                  name: cloudflared-tunnel-token
                  key: tunnel-token
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          livenessProbe:
            httpGet:
              path: /ready
              port: 2000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: 2000
            initialDelaySeconds: 5
            periodSeconds: 10
      restartPolicy: Always
{{- end }}
```

- [ ] **Step 4: Lint the chart**

```bash
helm lint charts/lolday
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add charts/lolday/templates/cloudflared-*.yaml
git commit -m "feat: add Cloudflare Tunnel deployment manifests"
```

---

### Task 10: End-to-End Infrastructure Verification

**Files:** None (verification only)

- [ ] **Step 1: Verify all cluster-level components**

```bash
echo "=== Cilium ==="
cilium status --wait

echo ""
echo "=== GPU Operator ==="
kubectl -n gpu-operator get pods --no-headers | awk '{print $1, $3}'

echo ""
echo "=== Volcano ==="
kubectl -n volcano-system get pods --no-headers | awk '{print $1, $3}'

echo ""
echo "=== GPU Resources ==="
kubectl get nodes -o jsonpath='{range .items[*]}Node: {.metadata.name} GPUs: {.status.allocatable.nvidia\.com/gpu}{"\n"}{end}'
```

Expected: All components Running, 2 GPUs allocatable.

- [ ] **Step 2: Verify lolday namespace components**

```bash
echo "=== Harbor ==="
kubectl -n lolday get pods -l app=harbor --no-headers | awk '{print $1, $3}'

echo ""
echo "=== All lolday pods ==="
kubectl -n lolday get pods
```

Expected: All Harbor pods Running.

- [ ] **Step 3: Test full GPU scheduling through Volcano**

Submit a job that uses Volcano scheduler with GPU:

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: batch.volcano.sh/v1alpha1
kind: Job
metadata:
  name: infra-final-test
  namespace: lolday
spec:
  minAvailable: 1
  schedulerName: volcano
  tasks:
    - replicas: 1
      name: gpu-task
      template:
        spec:
          containers:
            - name: test
              image: nvidia/cuda:12.6.3-base-ubuntu24.04
              command:
                - sh
                - -c
                - |
                  echo "=== GPU Test ==="
                  nvidia-smi
                  echo "=== Hostname ==="
                  hostname
                  echo "=== Test Complete ==="
              resources:
                limits:
                  nvidia.com/gpu: 1
          restartPolicy: Never
EOF
```

- [ ] **Step 4: Verify final test job**

```bash
# Wait for completion
kubectl -n lolday wait --for=condition=complete vcjob/infra-final-test --timeout=120s

# Check logs
kubectl -n lolday logs -l volcano.sh/job-name=infra-final-test
```

Expected: nvidia-smi output showing RTX 2080 Ti.

- [ ] **Step 5: Clean up test job**

```bash
kubectl -n lolday delete vcjob infra-final-test
```

- [ ] **Step 6: Print infrastructure summary**

```bash
echo ""
echo "============================================"
echo "  Lolday Infrastructure — Phase 1 Complete"
echo "============================================"
echo ""
echo "Cluster:        K3s $(kubectl version --short 2>/dev/null | grep Server | awk '{print $3}')"
echo "CNI:            Cilium"
echo "GPUs:           $(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}')x RTX 2080 Ti"
echo "Scheduler:      Volcano"
echo "Registry:       Harbor (lolday namespace)"
echo "NFS:            $(kubectl get storageclass nfs-datasets --no-headers 2>/dev/null && echo 'Configured' || echo 'Not configured')"
echo "Cloudflare:     $(kubectl -n lolday get deploy cloudflared --no-headers 2>/dev/null && echo 'Deployed' || echo 'Not deployed (enable after domain purchase)')"
echo ""
echo "Next: Phase 2 — Backend Core (FastAPI + PostgreSQL + Auth)"
echo ""
```
