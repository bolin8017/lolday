#!/usr/bin/env bash
# Phase 7.3 — Volcano Queue + scheduler ServiceMonitor contract test.
#
# Our chart renders:
#   1. A Queue CR `lolday-training` (weight=1, reclaimable=true) — holds all
#      training jobs submitted by the backend (batch.volcano.sh/v1alpha1 Job).
#   2. A ServiceMonitor `volcano-scheduler` in monitoring ns cross-ns-selecting
#      the volcano-scheduler-service on port `metrics` (8080) in volcano-system.
#      Same pattern as Phase 7.1.1 DCGM and Phase 7.2 Trivy SMs.
set -euo pipefail

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

# --- Queue ---
yq eval-all '
  select(.kind == "Queue" and .metadata.name == "lolday-training")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/queue.yaml"
[ -s "$TMPDIR/queue.yaml" ] \
  || fail "Queue 'lolday-training' not rendered — expected at charts/lolday/templates/volcano-queue.yaml"
pass "Queue 'lolday-training' rendered"

assert_queue_eq() {
  local expr="$1" expected="$2" msg="$3"
  local got
  got="$(yq eval "$expr" "$TMPDIR/queue.yaml")"
  [ "$got" = "$expected" ] || fail "$msg (got: '$got', expected: '$expected')"
}

assert_queue_eq '.apiVersion' 'scheduling.volcano.sh/v1beta1' \
  "Queue must use apiVersion scheduling.volcano.sh/v1beta1 (Volcano's stable Queue API)"
pass "Queue apiVersion: scheduling.volcano.sh/v1beta1"

assert_queue_eq '.spec.weight' '1' \
  "Queue weight must be 1 (only queue, weight-based fair-share irrelevant)"
assert_queue_eq '.spec.reclaimable' 'true' \
  "Queue reclaimable must be true — lets the scheduler take resources back from idle jobs in this queue"
pass "Queue spec: weight=1 reclaimable=true"

# --- ServiceMonitor for volcano-scheduler ---
yq eval-all '
  select(.kind == "ServiceMonitor" and .metadata.name == "volcano-scheduler")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/sm.yaml"
[ -s "$TMPDIR/sm.yaml" ] \
  || fail "ServiceMonitor 'volcano-scheduler' not rendered — expected at charts/lolday/templates/monitoring/servicemonitor-volcano.yaml"
pass "ServiceMonitor 'volcano-scheduler' rendered"

assert_sm_eq() {
  local expr="$1" expected="$2" msg="$3"
  local got
  got="$(yq eval "$expr" "$TMPDIR/sm.yaml")"
  [ "$got" = "$expected" ] || fail "$msg (got: '$got', expected: '$expected')"
}

assert_sm_eq '.metadata.namespace' 'monitoring' \
  "SM must live in monitoring ns (next to other lolday SMs)"
# Volcano subchart hardcodes `.Release.Namespace` across all its templates;
# without an override option it installs into the release ns (`lolday`) rather
# than the conventional `volcano-system`. SM cross-ns target matches that.
assert_sm_eq '.spec.namespaceSelector.matchNames[0]' 'lolday' \
  "namespaceSelector must target the release ns (lolday) — Volcano installs there, no override available"
pass "SM placement: monitoring ns, selects lolday (Volcano's install ns)"

# Volcano's Service labels use plain `app: volcano-scheduler` (not the
# app.kubernetes.io/component convention other charts follow). Verified via
# `kubectl -n lolday get svc lolday-scheduler-service -o jsonpath='{.metadata.labels}'`.
assert_sm_eq '.spec.selector.matchLabels.app' 'volcano-scheduler' \
  "selector must target app=volcano-scheduler (Volcano Service's actual label)"
pass "SM selector: app=volcano-scheduler"

assert_sm_eq '.spec.endpoints[0].port' 'metrics' \
  "endpoints[0].port must be 'metrics' (Volcano scheduler Service port name)"
assert_sm_eq '.spec.endpoints[0].path' '/metrics' \
  "endpoints[0].path must be '/metrics'"
assert_sm_eq '.spec.endpoints[0].interval' '30s' \
  "endpoints[0].interval must be '30s' (lolday SM convention)"
pass "SM endpoint: port=metrics, path=/metrics, interval=30s"

assert_sm_eq '.metadata.labels."app.kubernetes.io/name"' 'lolday' \
  "SM must carry lolday.labels for chart identity"
pass "lolday.labels applied"

echo ""
echo "All assertions passed."
