# Phase 1: Infrastructure Foundation (v2 — Simplified) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Replaces:** `2026-03-30-phase1-infrastructure.md` (over-engineered: Cilium broke SSH, Volcano/Harbor unnecessary at current scale)

**Goal:** Set up a working K3s cluster with GPU support, private container registry, training pod security isolation, and a Helm chart skeleton — using the simplest stack that meets real requirements.

**Architecture:** K3s with default Flannel CNI + built-in network policy controller (no Cilium). NVIDIA GPU Operator for GPU management. Lightweight registry:2 instead of Harbor. Standard Kubernetes NetworkPolicy for training pod isolation. No Volcano — use K8s native resource management.

**Tech Stack:** K3s (default), NVIDIA GPU Operator, Helm 3, registry:2, Cloudflare Tunnel

**Server:** server30 (Ubuntu 24.04, 2× RTX 2080 Ti 11GB, IP 140.118.155.30, SSH port 9453)

**Constraints:**
- `bolin8017` has no persistent sudo. Sudo is granted temporarily for cluster setup, then revoked.
- CLI tools must be installed to `~/.local/bin/` (user-level).
- **SSH (port 9453) must never be disrupted.** All network changes must be verified against SSH.
- System-level commands that require sudo are marked with `⚠️ SUDO` and must be given to the user to execute.

---

## File Structure

```
lolday/
├── charts/
│   └── lolday/
│       ├── Chart.yaml                     # Umbrella chart (no external dependencies)
│       ├── values.yaml                    # Default configuration
│       └── templates/
│           ├── _helpers.tpl               # Template helpers
│           ├── namespace.yaml             # lolday namespace
│           ├── registry.yaml              # registry:2 Deployment + Service + PV
│           ├── network-policy.yaml        # Training pod isolation
│           ├── cloudflared.yaml           # Cloudflare Tunnel Deployment (conditional)
│           └── cloudflared-secret.yaml    # Tunnel token Secret (conditional)
├── scripts/
│   ├── install-tools.sh                   # User-level CLI tools (no sudo)
│   ├── setup-k3s.sh                       # ⚠️ SUDO: K3s installation + kubeconfig copy
│   ├── deploy.sh                          # Deploy umbrella chart (no sudo)
│   └── teardown.sh                        # Teardown (partial sudo)
├── .gitignore
└── README.md
```

**Design decisions:**
- **No external Helm dependencies.** Harbor, NFS CSI, Cilium are all removed. The chart contains only our own templates, avoiding dependency version conflicts and reducing complexity.
- **Cluster-level components (K3s, GPU Operator) are installed via scripts/CLI.** Application-level components (registry, cloudflared) are managed by the Helm chart.
- **setup-k3s.sh is the ONLY script that requires sudo.** Everything else runs as the normal user.
- **K3s default installation** uses Flannel CNI + built-in kube-router network policy controller. No `--flannel-backend=none` or `--disable-network-policy` flags. Node goes `Ready` immediately — no CNI race condition.

---

### Task 1: Update Project Structure

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Update .gitignore**

Remove Harbor-specific patterns, add Helm generic patterns:

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

No change needed — current .gitignore is already correct.

- [ ] **Step 2: Rewrite README.md**

```markdown
# Lolday

Internal ML platform for ISLab malware detector management.

## Prerequisites

- NVIDIA drivers installed on host (`nvidia-smi` must work)
- Temporary sudo access for K3s installation

## Setup

```bash
# 1. Install CLI tools (no sudo)
bash scripts/install-tools.sh

# 2. Install K3s (requires sudo — run with a sudo-capable account)
sudo bash scripts/setup-k3s.sh

# 3. Install GPU Operator (no sudo)
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m

# 4. Deploy the platform (no sudo)
bash scripts/deploy.sh
```

## Teardown

```bash
bash scripts/teardown.sh
```

## Documentation

- [Design Spec](docs/superpowers/specs/2026-03-30-lolday-platform-design.md)
- [Phase 1 Plan (v2)](docs/superpowers/plans/2026-04-13-phase1-infrastructure-v2.md)
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: update README for simplified infrastructure"
```

---

### Task 2: Create CLI Tools Installation Script

**Files:**
- Create: `scripts/install-tools.sh`

This script installs kubectl, helm, and k9s to `~/.local/bin/` without sudo.

- [ ] **Step 1: Create scripts directory**

```bash
mkdir -p scripts
```

- [ ] **Step 2: Create install-tools.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

echo "=== Installing CLI tools to ${INSTALL_DIR} ==="
echo ""

# -------------------------------------------------------
# kubectl
# -------------------------------------------------------
echo "[1/3] kubectl..."
if command -v kubectl &>/dev/null; then
  echo "  Already installed: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>&1 | head -1)"
else
  KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt)
  curl -sLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
  chmod +x kubectl
  mv kubectl "${INSTALL_DIR}/"
  echo "  Installed: ${KUBECTL_VERSION}"
fi

# -------------------------------------------------------
# helm
# -------------------------------------------------------
echo "[2/3] helm..."
if command -v helm &>/dev/null; then
  echo "  Already installed: $(helm version --short 2>/dev/null)"
else
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | \
    HELM_INSTALL_DIR="$INSTALL_DIR" USE_SUDO=false bash
fi

# -------------------------------------------------------
# k9s
# -------------------------------------------------------
echo "[3/3] k9s..."
if command -v k9s &>/dev/null; then
  echo "  Already installed: $(k9s version --short 2>/dev/null || echo 'yes')"
else
  K9S_VERSION="v0.50.18"
  curl -sL "https://github.com/derailed/k9s/releases/download/${K9S_VERSION}/k9s_Linux_amd64.tar.gz" | \
    tar xz -C "${INSTALL_DIR}" k9s
  echo "  Installed: ${K9S_VERSION}"
fi

echo ""
echo "=== Done ==="
echo ""
echo "Make sure ${INSTALL_DIR} is in your PATH."
echo "Add to ~/.zshrc or ~/.bashrc if needed:"
echo "  export PATH=\"\${HOME}/.local/bin:\${PATH}\""
```

- [ ] **Step 3: Make executable**

```bash
chmod +x scripts/install-tools.sh
```

- [ ] **Step 4: Verify script runs**

```bash
bash scripts/install-tools.sh
```

Expected: kubectl, helm, k9s installed to `~/.local/bin/`. Each command should be callable.

- [ ] **Step 5: Commit**

```bash
git add scripts/install-tools.sh
git commit -m "feat: add user-level CLI tools installation script"
```

---

### Task 3: Create K3s Installation Script

**Files:**
- Create: `scripts/setup-k3s.sh`

This script requires sudo. It installs K3s with default settings and copies the kubeconfig to the invoking user.

- [ ] **Step 1: Create setup-k3s.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Must be run as root (sudo)
if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This script must be run with sudo."
  echo "Usage: sudo bash scripts/setup-k3s.sh"
  exit 1
fi

# Detect the real user (not root) who invoked sudo
REAL_USER="${SUDO_USER:-}"
if [ -z "$REAL_USER" ]; then
  echo "Error: Cannot determine the real user. Run with: sudo bash scripts/setup-k3s.sh"
  exit 1
fi
REAL_HOME=$(eval echo "~${REAL_USER}")

echo "=== K3s Cluster Setup ==="
echo "User: ${REAL_USER}"
echo "Home: ${REAL_HOME}"
echo ""

# -------------------------------------------------------
# Pre-flight: Verify SSH will not be affected
# -------------------------------------------------------
echo "[0/3] Pre-flight checks..."
SSH_PORT=$(grep -E '^Port ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}')
SSH_PORT="${SSH_PORT:-22}"
echo "  SSH port: ${SSH_PORT}"
echo "  SSH service: $(systemctl is-active ssh)"
if ! systemctl is-active ssh &>/dev/null; then
  echo "  ERROR: SSH is not running. Aborting to prevent lockout."
  exit 1
fi
echo "  Pre-flight OK"
echo ""

# -------------------------------------------------------
# 1. Install K3s with default settings
# -------------------------------------------------------
echo "[1/3] Installing K3s (default Flannel + network policy)..."
if systemctl is-active k3s &>/dev/null; then
  echo "  K3s already running. Skipping installation."
else
  curl -sfL https://get.k3s.io | sh -
  echo "  Waiting for K3s to be ready..."
  until kubectl get nodes &>/dev/null; do
    sleep 2
  done
fi
echo "  K3s installed"
echo ""

# -------------------------------------------------------
# 2. Copy kubeconfig to user
# -------------------------------------------------------
echo "[2/3] Setting up kubeconfig for ${REAL_USER}..."
KUBE_DIR="${REAL_HOME}/.kube"
mkdir -p "$KUBE_DIR"
cp /etc/rancher/k3s/k3s.yaml "${KUBE_DIR}/config"
chown "${REAL_USER}:$(id -gn "$REAL_USER")" "${KUBE_DIR}/config"
chmod 600 "${KUBE_DIR}/config"
echo "  Kubeconfig written to ${KUBE_DIR}/config"
echo ""

# -------------------------------------------------------
# 3. Post-flight: Verify SSH still works
# -------------------------------------------------------
echo "[3/3] Post-flight checks..."
echo "  SSH service: $(systemctl is-active ssh)"
echo "  K3s service: $(systemctl is-active k3s)"
echo "  Node status:"
kubectl get nodes
echo ""

echo "=== K3s setup complete ==="
echo ""
echo "You can now run kubectl as ${REAL_USER} (no sudo needed)."
echo "Next step: install GPU Operator (see README.md)"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/setup-k3s.sh
```

- [ ] **Step 3: Commit (do NOT run yet — requires sudo)**

```bash
git add scripts/setup-k3s.sh
git commit -m "feat: add K3s installation script (sudo required)"
```

- [ ] **Step 4: ⚠️ SUDO — User runs K3s installation**

The user must run this with a sudo-capable account:

```bash
sudo bash scripts/setup-k3s.sh
```

Expected:
- K3s installs and starts
- Node shows `Ready` status immediately (Flannel is built-in, no CNI wait)
- `~/.kube/config` is created with correct ownership
- SSH service confirmed active

- [ ] **Step 5: Verify kubectl works without sudo**

```bash
kubectl get nodes
```

Expected:
```
NAME       STATUS   ROLES                  AGE   VERSION
server30   Ready    control-plane,master   ...   v1.x.x+k3s1
```

- [ ] **Step 6: Verify SSH was not affected**

```bash
systemctl is-active ssh
ss -tlnp | grep 9453
```

Expected: `active` and SSH listening on port 9453.

---

### Task 4: Install NVIDIA GPU Operator

**Files:** None (Helm operation, no sudo required)

**Prerequisites:** K3s running, kubectl working, helm installed

- [ ] **Step 1: Add NVIDIA Helm repo**

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update
```

- [ ] **Step 2: Install GPU Operator**

Host already has NVIDIA drivers (`nvidia-smi` works), so disable the driver container:

```bash
helm install gpu-operator nvidia/gpu-operator \
  -n gpu-operator --create-namespace \
  --set driver.enabled=false \
  --set toolkit.enabled=true \
  --set devicePlugin.enabled=true \
  --set dcgmExporter.enabled=true \
  --wait --timeout 5m
```

- [ ] **Step 3: Wait for GPU Operator pods**

```bash
kubectl -n gpu-operator get pods -w
```

Wait until all pods are Running or Completed:
- `nvidia-device-plugin-daemonset-*` — Running
- `nvidia-container-toolkit-daemonset-*` — Running
- `gpu-operator-*` — Running
- `dcgm-exporter-*` — Running

- [ ] **Step 4: Verify GPUs are registered**

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable}' | python3 -m json.tool | grep nvidia
```

Expected:
```
"nvidia.com/gpu": "2"
```

- [ ] **Step 5: Test GPU access in a Pod**

```bash
kubectl run gpu-test --rm -it --restart=Never \
  --image=nvidia/cuda:12.6.3-base-ubuntu24.04 \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi
```

Expected: `nvidia-smi` output showing one RTX 2080 Ti. Pod auto-deletes.

---

### Task 5: Create Helm Umbrella Chart

**Files:**
- Create: `charts/lolday/Chart.yaml`
- Create: `charts/lolday/values.yaml`
- Create: `charts/lolday/templates/_helpers.tpl`
- Create: `charts/lolday/templates/namespace.yaml`
- Create: `charts/lolday/templates/registry.yaml`
- Create: `charts/lolday/templates/network-policy.yaml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p charts/lolday/templates
```

- [ ] **Step 2: Create Chart.yaml**

No external dependencies — all templates are ours:

```yaml
apiVersion: v2
name: lolday
description: ISLab Malware Detector Management Platform
type: application
version: 0.1.0
appVersion: "0.1.0"

# No dependencies — Harbor, NFS CSI, Cilium removed.
# registry:2 and cloudflared are managed as our own templates.
```

- [ ] **Step 3: Create values.yaml**

```yaml
# =============================================================================
# Lolday Platform Configuration
# =============================================================================

global:
  namespace: lolday

# =============================================================================
# Private Container Registry (registry:2)
# =============================================================================
registry:
  enabled: true
  storage:
    size: 50Gi
    # Uses K3s default StorageClass (local-path)

# =============================================================================
# Training Pod Security
# =============================================================================
training:
  # NetworkPolicy: deny all egress from training pods
  networkPolicy:
    enabled: true

# =============================================================================
# Cloudflare Tunnel
# =============================================================================
cloudflare:
  enabled: false  # Enable after domain is purchased
  tunnelToken: "" # Set via --set at deploy time, NEVER commit
  replicas: 2
```

- [ ] **Step 4: Create templates/_helpers.tpl**

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

- [ ] **Step 5: Create templates/namespace.yaml**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
```

- [ ] **Step 6: Create templates/registry.yaml**

```yaml
{{- if .Values.registry.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: registry-data
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: registry
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: {{ .Values.registry.storage.size }}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: registry
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/component: registry
  template:
    metadata:
      labels:
        app.kubernetes.io/component: registry
    spec:
      containers:
        - name: registry
          image: registry:2
          ports:
            - containerPort: 5000
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: registry-data
---
apiVersion: v1
kind: Service
metadata:
  name: registry
  namespace: {{ .Values.global.namespace }}
  labels:
    app.kubernetes.io/component: registry
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  ports:
    - port: 5000
      targetPort: 5000
  selector:
    app.kubernetes.io/component: registry
{{- end }}
```

- [ ] **Step 7: Create templates/network-policy.yaml**

This denies all egress from training pods (labeled `lolday.io/role: training`). Training pods cannot reach the internet, DNS, or K8s API.

```yaml
{{- if .Values.training.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-training-egress
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      lolday.io/role: training
  policyTypes:
    - Egress
  egress: []
{{- end }}
```

- [ ] **Step 8: Lint the chart**

```bash
helm lint charts/lolday
```

Expected: `0 chart(s) failed`

- [ ] **Step 9: Commit**

```bash
git add charts/
git commit -m "feat: add simplified Helm umbrella chart with registry and NetworkPolicy"
```

---

### Task 6: Cloudflare Tunnel Templates

**Files:**
- Create: `charts/lolday/templates/cloudflared-secret.yaml`
- Create: `charts/lolday/templates/cloudflared.yaml`

Templates only — actual deployment happens after the user buys a domain and creates a tunnel in the Cloudflare dashboard.

- [ ] **Step 1: Create cloudflared-secret.yaml**

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

- [ ] **Step 2: Create cloudflared.yaml**

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
          image: cloudflare/cloudflared:latest
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

- [ ] **Step 3: Lint**

```bash
helm lint charts/lolday
```

Expected: `0 chart(s) failed`

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/templates/cloudflared*.yaml
git commit -m "feat: add Cloudflare Tunnel deployment templates"
```

---

### Task 7: Create Deployment Scripts

**Files:**
- Create: `scripts/deploy.sh`
- Create: `scripts/teardown.sh`

- [ ] **Step 1: Create deploy.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$SCRIPT_DIR/../charts/lolday"

echo "=== Lolday Platform Deploy ==="
echo ""

# Pre-flight
echo "[1/2] Pre-flight checks..."
if ! kubectl get nodes &>/dev/null; then
  echo "  ERROR: Cannot reach K8s API. Is K3s running?"
  exit 1
fi
echo "  Cluster OK"

GPU_COUNT=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "0")
echo "  GPUs available: ${GPU_COUNT}"
echo ""

# Deploy
echo "[2/2] Deploying lolday..."
helm upgrade --install lolday "$CHART_DIR" \
  -n lolday --create-namespace \
  --set cloudflare.enabled="${CF_ENABLED:-false}" \
  --set cloudflare.tunnelToken="${CF_TUNNEL_TOKEN:-}" \
  --wait --timeout 5m

echo ""
echo "=== Deploy complete ==="
kubectl -n lolday get pods
```

- [ ] **Step 2: Create teardown.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Lolday Teardown ==="
echo ""
echo "This will remove the lolday Helm release."
echo "PersistentVolumes with Retain policy will be kept."
echo ""
read -p "Continue? [y/N] " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 0
fi

echo "[1/2] Removing lolday Helm release..."
helm uninstall lolday -n lolday 2>/dev/null || echo "  Not installed, skipping."

echo "[2/2] Removing GPU Operator..."
helm uninstall gpu-operator -n gpu-operator 2>/dev/null || echo "  Not installed, skipping."

echo ""
echo "=== Teardown complete ==="
echo ""
echo "K3s is still running. To fully remove K3s (requires sudo):"
echo "  sudo /usr/local/bin/k3s-uninstall.sh"
```

- [ ] **Step 3: Make scripts executable**

```bash
chmod +x scripts/deploy.sh scripts/teardown.sh
```

- [ ] **Step 4: Commit**

```bash
git add scripts/deploy.sh scripts/teardown.sh
git commit -m "feat: add deploy and teardown scripts"
```

---

### Task 8: Deploy and End-to-End Verification

**Files:** None (operations only)

**Prerequisites:** Tasks 1–7 complete, K3s running, GPU Operator installed

- [ ] **Step 1: Deploy the platform**

```bash
bash scripts/deploy.sh
```

Expected: Helm release installed, registry pod Running.

- [ ] **Step 2: Verify all cluster components**

```bash
echo "=== Nodes ==="
kubectl get nodes -o wide

echo ""
echo "=== GPU ==="
kubectl get nodes -o jsonpath='{range .items[*]}Node: {.metadata.name}  GPUs: {.status.allocatable.nvidia\.com/gpu}{"\n"}{end}'

echo ""
echo "=== lolday namespace ==="
kubectl -n lolday get all

echo ""
echo "=== GPU Operator ==="
kubectl -n gpu-operator get pods --no-headers | awk '{print $1, $3}'
```

Expected:
- Node `server30` is `Ready`
- 2 GPUs allocatable
- registry pod Running in lolday namespace
- GPU Operator pods Running

- [ ] **Step 3: Verify registry is functional**

```bash
kubectl -n lolday port-forward svc/registry 5000:5000 &
sleep 2
curl -s http://localhost:5000/v2/_catalog
kill %1
```

Expected: `{"repositories":[]}` — empty registry, ready to accept images.

- [ ] **Step 4: Verify NetworkPolicy is applied**

```bash
kubectl -n lolday get networkpolicy
```

Expected: `deny-training-egress` policy exists.

- [ ] **Step 5: Test GPU scheduling**

```bash
kubectl run gpu-e2e-test --rm -it --restart=Never \
  -n lolday \
  --image=nvidia/cuda:12.6.3-base-ubuntu24.04 \
  --limits=nvidia.com/gpu=1 \
  -- nvidia-smi
```

Expected: `nvidia-smi` output showing one RTX 2080 Ti.

- [ ] **Step 6: Test NetworkPolicy blocks training pod egress**

```bash
kubectl run netpol-test --rm -it --restart=Never \
  -n lolday \
  --labels="lolday.io/role=training" \
  --image=busybox:1.36 \
  -- sh -c "wget -T 5 -q http://google.com -O /dev/null && echo 'FAIL: egress allowed' || echo 'PASS: egress blocked'"
```

Expected: `PASS: egress blocked` — the NetworkPolicy denies all egress from training-labeled pods.

- [ ] **Step 7: Verify SSH is still working**

```bash
systemctl is-active ssh
ss -tlnp | grep 9453
```

Expected: SSH active on port 9453.

- [ ] **Step 8: Print summary**

```bash
echo ""
echo "============================================"
echo "  Lolday Infrastructure — Phase 1 Complete"
echo "============================================"
echo ""
echo "Cluster:     K3s $(kubectl version --short 2>/dev/null | grep Server | awk '{print $3}' || kubectl version 2>&1 | grep 'Server Version' | awk '{print $3}')"
echo "CNI:         Flannel (K3s default)"
echo "NetworkPolicy: K3s built-in kube-router"
echo "GPUs:        $(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}')× RTX 2080 Ti"
echo "Registry:    registry:2 (lolday namespace)"
echo "Cloudflare:  $(kubectl -n lolday get deploy cloudflared --no-headers 2>/dev/null && echo 'Deployed' || echo 'Not deployed (enable after domain purchase)')"
echo ""
echo "Next: Phase 2 — Backend Core (FastAPI + PostgreSQL + Auth)"
echo ""
```
