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
