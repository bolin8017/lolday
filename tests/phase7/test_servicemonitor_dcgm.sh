#!/usr/bin/env bash
# Phase 7.1.1 — DCGM ServiceMonitor contract test.
#
# Phase 6 advertised GPU-temp monitoring via the GPUTemperatureHigh alert on
# DCGM_FI_DEV_GPU_TEMP, but shipped no ServiceMonitor targeting the nvidia-dcgm-
# exporter Service in the gpu-operator ns — so the metric was never scraped and
# the alert could not fire. This test pins the SM that closes that gap.
set -euo pipefail

# cwd-independent repo root (see test_alertmanager_discord.sh for rationale).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CHART="$REPO_ROOT/charts/lolday"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "✗ FAIL: $*" >&2; exit 1; }
pass() { echo "✓ $*"; }

for bin in helm yq; do
  command -v "$bin" >/dev/null || fail "required tool not on PATH: $bin"
done

# --- Step 1: render chart with dummy required values ---
helm template lolday "$CHART" \
  --namespace lolday \
  --set monitoring.postgresExporter.password=dummy \
  --set monitoring.grafana.adminPassword=dummy \
  --set mlflow.db.password=dummy \
  --set backend.harborAdminPassword=dummy \
  --set backend.fernetKey=dummy \
  --set cloudflare.enabled=false \
  > "$TMPDIR/rendered.yaml" 2> "$TMPDIR/render.err" \
  || { cat "$TMPDIR/render.err" >&2; fail "helm template failed"; }
pass "chart renders"

# --- Step 2: extract the nvidia-dcgm-exporter ServiceMonitor ---
yq eval-all '
  select(.kind == "ServiceMonitor" and .metadata.name == "nvidia-dcgm-exporter")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/sm.yaml"
[ -s "$TMPDIR/sm.yaml" ] \
  || fail "ServiceMonitor 'nvidia-dcgm-exporter' not rendered — expected at charts/lolday/templates/monitoring/servicemonitor-dcgm.yaml"
pass "ServiceMonitor 'nvidia-dcgm-exporter' rendered"

assert_eq() {
  local expr="$1" expected="$2" msg="$3"
  local got
  got="$(yq eval "$expr" "$TMPDIR/sm.yaml")"
  [ "$got" = "$expected" ] || fail "$msg (got: '$got', expected: '$expected')"
}

# --- Step 3: SM must be in the monitoring ns (where kps Prometheus lives) ---
assert_eq '.metadata.namespace' 'monitoring' \
  "SM must be in monitoring ns (kps Prometheus serviceMonitorNamespaceSelector cluster-wide, but co-locating with other SMs is the project convention)"
pass "SM in monitoring ns"

# --- Step 4: namespaceSelector pins gpu-operator (where dcgm-exporter Service lives) ---
assert_eq '.spec.namespaceSelector.matchNames | length' '1' \
  "namespaceSelector.matchNames must have exactly one entry"
assert_eq '.spec.namespaceSelector.matchNames[0]' 'gpu-operator' \
  "namespaceSelector.matchNames[0] must be 'gpu-operator' (NVIDIA GPU Operator deploys dcgm-exporter there)"
pass "namespaceSelector: gpu-operator"

# --- Step 5: selector picks the dcgm-exporter Service ---
assert_eq '.spec.selector.matchLabels.app' 'nvidia-dcgm-exporter' \
  "selector.matchLabels.app must be 'nvidia-dcgm-exporter' (the Service's own label — verified via kubectl get svc nvidia-dcgm-exporter -n gpu-operator -o yaml)"
pass "selector: app=nvidia-dcgm-exporter"

# --- Step 6: endpoint scrapes the gpu-metrics port / /metrics ---
assert_eq '.spec.endpoints | length' '1' \
  "exactly one endpoint — DCGM only exposes one /metrics port"
assert_eq '.spec.endpoints[0].port' 'gpu-metrics' \
  "endpoints[0].port must be 'gpu-metrics' (the Service port name, 9400/TCP)"
assert_eq '.spec.endpoints[0].path' '/metrics' \
  "endpoints[0].path must be '/metrics'"
assert_eq '.spec.endpoints[0].interval' '30s' \
  "endpoints[0].interval must be '30s' (matches other lolday SMs)"
pass "endpoint: port gpu-metrics, path /metrics, interval 30s"

# --- Step 7: kps-compatible labels (operator picks up by discovery) ---
# lolday.labels helper sets app.kubernetes.io/* and is the project convention for
# ServiceMonitors we ship. Absence suggests a copy-paste template that skipped it.
assert_eq '.metadata.labels."app.kubernetes.io/name"' 'lolday' \
  "SM must carry the lolday.labels chart identity"
pass "lolday.labels applied (consistent with other ServiceMonitors)"

echo ""
echo "All assertions passed."
