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
READY=$(kubectl -n lolday get pod -l app=frontend -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "")
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
