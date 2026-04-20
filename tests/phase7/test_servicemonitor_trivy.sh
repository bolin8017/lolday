#!/usr/bin/env bash
# Phase 7.2 — Trivy Operator ServiceMonitor contract test.
#
# Trivy Operator ships a Service `trivy-operator` in trivy-system (port name
# `metrics`, targetPort 8080 on container). Our chart adds a ServiceMonitor in
# `monitoring` ns that cross-namespaceSelects it, following the same pattern
# as Phase 7.1.1's DCGM SM.
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

yq eval-all '
  select(.kind == "ServiceMonitor" and .metadata.name == "trivy-operator")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/sm.yaml"
[ -s "$TMPDIR/sm.yaml" ] \
  || fail "ServiceMonitor 'trivy-operator' not rendered — expected at charts/lolday/templates/monitoring/servicemonitor-trivy.yaml"
pass "ServiceMonitor 'trivy-operator' rendered"

assert_eq() {
  local expr="$1" expected="$2" msg="$3"
  local got
  got="$(yq eval "$expr" "$TMPDIR/sm.yaml")"
  [ "$got" = "$expected" ] || fail "$msg (got: '$got', expected: '$expected')"
}

assert_eq '.metadata.namespace' 'monitoring' \
  "SM must live in monitoring ns (next to other lolday SMs)"
pass "SM in monitoring ns"

assert_eq '.spec.namespaceSelector.matchNames | length' '1' \
  "namespaceSelector must pin a single ns"
assert_eq '.spec.namespaceSelector.matchNames[0]' 'trivy-system' \
  "namespaceSelector must be trivy-system (Trivy Operator default ns)"
pass "namespaceSelector: trivy-system"

# Trivy Service's labels expose app.kubernetes.io/name: trivy-operator
# (verified via helm template aqua/trivy-operator). Select on that label only;
# avoid .../instance so helm release-name changes don't break scraping.
assert_eq '.spec.selector.matchLabels."app.kubernetes.io/name"' 'trivy-operator' \
  "selector must match the Trivy Service on app.kubernetes.io/name"
pass "selector: app.kubernetes.io/name=trivy-operator"

assert_eq '.spec.endpoints | length' '1' \
  "exactly one endpoint — Trivy exposes one /metrics port"
assert_eq '.spec.endpoints[0].port' 'metrics' \
  "endpoints[0].port must be 'metrics' (Trivy chart's Service port name)"
assert_eq '.spec.endpoints[0].path' '/metrics' \
  "endpoints[0].path must be '/metrics'"
assert_eq '.spec.endpoints[0].interval' '30s' \
  "endpoints[0].interval must be '30s' (lolday SM convention)"
pass "endpoint: port=metrics, path=/metrics, interval=30s"

assert_eq '.metadata.labels."app.kubernetes.io/name"' 'lolday' \
  "SM must carry lolday.labels for chart identity"
pass "lolday.labels applied"

echo ""
echo "All assertions passed."
