#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="$SCRIPT_DIR/../charts/lolday"

echo "=== Lolday Platform Deploy ==="
echo ""

# Required secrets
: "${HARBOR_ADMIN_PASSWORD:?HARBOR_ADMIN_PASSWORD must be set — generate with: openssl rand -base64 24}"
: "${FERNET_KEY:?FERNET_KEY must be set — generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'}"
: "${PG_PASSWORD:?PG_PASSWORD must be set — generate with: openssl rand -base64 24}"
: "${JWT_SECRET:?JWT_SECRET must be set — generate with: openssl rand -base64 48}"
: "${ADMIN_EMAIL:?ADMIN_EMAIL must be set (e.g. admin@lolday.dev)}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"
: "${MLFLOW_DB_PASSWORD:?MLFLOW_DB_PASSWORD must be set — generate with: openssl rand -base64 32 | tr -d '=+/'}"
: "${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD must be set — generate with: openssl rand -base64 32 | tr -d '=+/'}"
: "${PG_EXPORTER_PASSWORD:?PG_EXPORTER_PASSWORD must be set — generate with: openssl rand -base64 32 | tr -d '=+/'}"

# Backend image (overridable for Phase 5/6). Default tracks the latest deployed phase.
BACKEND_IMAGE=${BACKEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-backend:phase6}
FRONTEND_IMAGE=${FRONTEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-frontend:phase5}

# Pre-flight
echo "[1/4] Pre-flight checks..."
if ! kubectl get nodes &>/dev/null; then
  echo "  ERROR: Cannot reach K8s API. Is K3s running?"
  exit 1
fi
echo "  Cluster OK"

GPU_COUNT=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "0")
echo "  GPUs available: ${GPU_COUNT}"
echo ""

# Harbor repo + dependency build
echo "[2/4] Preparing Helm dependencies..."
helm repo add harbor https://helm.goharbor.io 2>/dev/null || true
helm repo update >/dev/null
(cd "$CHART_DIR" && helm dependency build)
echo "  Dependencies built"
echo ""

# Ensure namespaces
echo "[3/4] Ensuring namespaces..."
kubectl create namespace lolday --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "  Namespaces ready"
echo ""

# Deploy
echo "[4/4] Deploying lolday..."
helm upgrade --install lolday "$CHART_DIR" \
  -n lolday \
  --set cloudflare.enabled="${CF_ENABLED:-false}" \
  --set cloudflare.tunnelToken="${CF_TUNNEL_TOKEN:-}" \
  --set postgresql.auth.password="$PG_PASSWORD" \
  --set backend.jwtSecret="$JWT_SECRET" \
  --set backend.firstAdmin.email="$ADMIN_EMAIL" \
  --set backend.firstAdmin.password="$ADMIN_PASSWORD" \
  --set backend.fernetKey="$FERNET_KEY" \
  --set backend.harborAdminPassword="$HARBOR_ADMIN_PASSWORD" \
  --set backend.image="$BACKEND_IMAGE" \
  --set frontend.image="$FRONTEND_IMAGE" \
  --set harbor.harborAdminPassword="$HARBOR_ADMIN_PASSWORD" \
  --set mlflow.db.password="$MLFLOW_DB_PASSWORD" \
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  --set monitoring.postgresExporter.password="$PG_EXPORTER_PASSWORD" \
  --wait --timeout 10m

echo ""
echo "=== Deploy complete ==="
kubectl -n lolday get pods
echo ""
cat <<EOF

=========================================================================
  NEXT MANUAL STEP (requires sudo):

    sudo bash scripts/patch-k3s-registries.sh

  This configures K3s containerd to resolve 'harbor.lolday.svc:80' as
  the in-cluster Harbor. The script is safe: it backs up registries.yaml,
  diffs the change, and auto-rolls back if k3s fails to restart.

  Without this step, detector builds cannot push images to Harbor and
  the platform cannot pull build-helper / detector images.
=========================================================================
EOF

# =============================================================================
# Phase 4: Dataset & Jobs
# =============================================================================

echo "=== Phase 4: pre-deploy checks ==="
"$(dirname "$0")/phase4-pre-deploy-check.sh"

echo "=== Phase 4: wait for MLflow ==="
kubectl -n lolday wait deploy/mlflow --for=condition=Available --timeout=180s

echo "=== Phase 4: smoke test MLflow from backend pod ==="
kubectl -n lolday exec deploy/backend -- curl -sf http://mlflow.lolday.svc:5000/health || \
  echo "WARN: MLflow /health failed — may still be initializing. Check 'kubectl -n lolday logs deploy/mlflow'."

echo
echo "Phase 4 deploy complete."
