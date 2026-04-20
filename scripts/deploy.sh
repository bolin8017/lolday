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
: "${DISCORD_WEBHOOK_URL_CRITICAL:?DISCORD_WEBHOOK_URL_CRITICAL must be set — webhook URL for #lolday-alerts-critical}"
: "${DISCORD_WEBHOOK_URL_WARNING:?DISCORD_WEBHOOK_URL_WARNING must be set — webhook URL for #lolday-alerts-warning}"
# Reject obvious typos / wrong-service pastes before kubectl apply. A malformed
# webhook URL silently creates a Secret that only fails at alert-dispatch time
# (Discord returns 401), defeating "alerts must reach the human".
for _var in DISCORD_WEBHOOK_URL_CRITICAL DISCORD_WEBHOOK_URL_WARNING; do
  _url="${!_var}"
  [[ "$_url" =~ ^https://(discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9_-]+$ ]] \
    || { echo "  ERROR: $_var is not a valid Discord webhook URL shape" >&2; exit 1; }
done
unset _var _url

# Backend image (overridable for Phase 5/6). Default tracks the latest deployed phase.
BACKEND_IMAGE=${BACKEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-backend:phase6.3}
FRONTEND_IMAGE=${FRONTEND_IMAGE:-harbor.lolday.svc:80/lolday/lolday-frontend:phase5}

# Pre-flight
echo "[1/4] Pre-flight checks..."
if ! kubectl get nodes &>/dev/null; then
  echo "  ERROR: Cannot reach K8s API. Is K3s running?"
  exit 1
fi
echo "  Cluster OK"

GPU_COUNT=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || echo "")
if [ -z "$GPU_COUNT" ]; then
  echo "  WARN: could not query GPU allocatable (jsonpath failed — kubectl auth OK?)"
  GPU_COUNT=0
elif [ "$GPU_COUNT" = "0" ]; then
  echo "  WARN: 0 GPUs allocatable — training Jobs will stay Pending"
fi
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
# Phase 6: kube-prometheus-stack's pre-upgrade hook creates a ServiceAccount in
# monitoring ns before helm applies the Namespace template. Pre-create + mark as
# Helm-owned so the upgrade can adopt it.
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl label ns monitoring app.kubernetes.io/managed-by=Helm --overwrite >/dev/null
kubectl annotate ns monitoring meta.helm.sh/release-name=lolday meta.helm.sh/release-namespace=lolday --overwrite >/dev/null
# Phase 6: kps CRDs must be registered BEFORE helm applies PrometheusRule /
# ServiceMonitor instances. Apply them up-front from the fetched subchart tarball.
# Fail fast if the tarball isn't there — otherwise helm upgrade below hits a
# confusing 'no matches for kind' error mid-apply.
KPS_TGZ=$(ls "$CHART_DIR/charts/"kube-prometheus-stack-*.tgz 2>/dev/null | tail -1 || true)
if [ -z "$KPS_TGZ" ]; then
  echo "  ERROR: kube-prometheus-stack tarball missing under $CHART_DIR/charts/ — helm dependency build did not produce it." >&2
  exit 1
fi
KPS_CRD_DIR=$(mktemp -d)
tar xzf "$KPS_TGZ" -C "$KPS_CRD_DIR"
kubectl apply --server-side -f "$KPS_CRD_DIR"/kube-prometheus-stack/charts/crds/crds/
rm -rf "$KPS_CRD_DIR"

# Phase 7.1: Alertmanager Discord webhook Secret. Referenced by the
# AlertmanagerConfig CR `discord-receivers` (see templates/monitoring/alertmanager-config-discord.yaml)
# via apiURL.name/key SecretKeySelector, so the Prometheus Operator resolves
# these webhook URLs when building the runtime Alertmanager config. Must exist
# in the monitoring ns (same as Alertmanager pod + AC CR) before helm upgrade.
kubectl -n monitoring create secret generic alertmanager-discord \
  --from-literal=webhook-url-critical="$DISCORD_WEBHOOK_URL_CRITICAL" \
  --from-literal=webhook-url-warning="$DISCORD_WEBHOOK_URL_WARNING" \
  --dry-run=client -o yaml | kubectl apply -f -
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
  --wait --timeout 20m

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
