#!/usr/bin/env bash
# Phase 6 pre-deploy check: verify disk, secrets, DNS, and (if previously
# deployed) that Phase 4/5 workloads are healthy before the upgrade.
set -euo pipefail

CHART_DIR="$(cd "$(dirname "$0")/../charts/lolday" && pwd)"
FAIL=0    # collected so a WARN-level condition can still bubble up at the end

echo "=== Phase 6 pre-deploy check ==="

# --- StorageClass + disk space ---
# The 60 Gi threshold is sized for a fresh monitoring install
# (Prometheus 20 + Loki 30 + Alertmanager 2 + Grafana 5 + headroom). Once PVCs
# are allocated this gives false positives — override by `SKIP_DISK_CHECK=1`.
echo "[1/6] K3s local-path SC + root disk space ..."
if ! kubectl get sc local-path >/dev/null 2>&1; then
  echo "  FAIL — local-path StorageClass missing (K3s default)"
  exit 1
fi
if [ "${SKIP_DISK_CHECK:-0}" != "1" ]; then
  FREE_G=$(df -BG --output=avail / | tail -1 | tr -d ' G')
  if [ "$FREE_G" -lt 60 ]; then
    echo "  FAIL — $FREE_G Gi free on / (< 60 Gi required for monitoring PVs; SKIP_DISK_CHECK=1 to bypass)"
    exit 1
  fi
  echo "  OK ($FREE_G Gi free on /)"
else
  echo "  OK (disk check skipped)"
fi

# --- Secrets ---
echo "[2/6] secrets in env ..."
: "${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD in ~/.lolday-secrets.env}"
: "${PG_EXPORTER_PASSWORD:?set PG_EXPORTER_PASSWORD in ~/.lolday-secrets.env}"
echo "  OK"

# --- Tunnel + DNS (only strict for sub-phase 6-3) ---
echo "[3/6] Tunnel config ..."
if [ "${CF_ENABLED:-false}" = "true" ]; then
  : "${CF_TUNNEL_TOKEN:?CF_ENABLED=true but CF_TUNNEL_TOKEN not set}"
  # CNAME should resolve through Cloudflare edge (proxied → anycast IPs);
  # an empty reply means the record isn't set yet.
  DIG_OUT=$(dig +short lolday.connlabai.com 2>/dev/null || true)
  if [ -z "$DIG_OUT" ]; then
    echo "  FAIL — CF_ENABLED=true but lolday.connlabai.com does not resolve (create the Tunnel Public Hostname first)"
    exit 1
  fi
fi
echo "  OK"

# --- Phase 4 backend health (skipped on first install) ---
echo "[4/6] backend reachable via in-cluster port-forward ..."
if ! kubectl -n lolday get deploy backend >/dev/null 2>&1; then
  echo "  SKIP — backend Deployment not present yet (first install)"
else
  PF_PID=""
  PF_LOG=$(mktemp)
  cleanup() {
    [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null || true
    rm -f "$PF_LOG"
  }
  trap cleanup EXIT
  kubectl -n lolday port-forward svc/backend 18999:8000 >"$PF_LOG" 2>&1 &
  PF_PID=$!
  # Poll up to 10s instead of a fixed sleep — K8s API latency varies.
  BACKEND_OK=0
  for _ in $(seq 1 20); do
    if curl -fsS http://localhost:18999/docs >/dev/null 2>&1; then
      BACKEND_OK=1
      break
    fi
    sleep 0.5
  done
  if [ "$BACKEND_OK" -ne 1 ]; then
    echo "  FAIL — backend not responding. port-forward log:"
    sed 's/^/    /' "$PF_LOG" >&2
    exit 1
  fi
  echo "  OK"
fi

# --- Phase 5 frontend health (skipped on first install) ---
echo "[5/6] frontend pod Ready ..."
if ! kubectl -n lolday get deploy frontend >/dev/null 2>&1; then
  echo "  SKIP — frontend Deployment not present yet (first install)"
else
  PODS=$(kubectl -n lolday get pod -l app=frontend -o name 2>&1) || {
    echo "  FAIL — kubectl error listing frontend pods:"
    echo "$PODS" | sed 's/^/    /' >&2
    exit 1
  }
  if [ -z "$PODS" ]; then
    echo "  FAIL — no pod matches label app=frontend (Deployment exists but pods are missing)"
    exit 1
  fi
  READY=$(kubectl -n lolday get pod -l app=frontend -o jsonpath='{.items[0].status.containerStatuses[0].ready}')
  if [ "$READY" != "true" ]; then
    echo "  FAIL — frontend pod exists but container is not Ready"
    exit 1
  fi
  echo "  OK"
fi

# --- Chart lint ---
# Stdout & stderr kept visible so WARN lines surface; exit status still caught
# by `set -e`.
echo "[6/6] helm lint ..."
helm lint "$CHART_DIR" \
  --set cloudflare.tunnelToken="${CF_TUNNEL_TOKEN:-placeholder}" \
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  --set monitoring.postgresExporter.password="$PG_EXPORTER_PASSWORD" \
  --set backend.fernetKey=ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg= \
  --set backend.harborAdminPassword=lint --set mlflow.db.password=lint \
  --set postgresql.auth.password=lint
echo "  OK"

echo ""
if [ "$FAIL" -ne 0 ]; then
  echo "=== $FAIL check(s) failed ==="
  exit 1
fi
echo "=== All checks passed ==="
