#!/usr/bin/env bash
# 議題 B (2026-05-10 alerting redesign) — chart contract test for the
# PrometheusRule inventory.  Asserts:
#   1. exactly 16 alerts present in the lolday-baseline PrometheusRule
#   2. the 2 removed rules are absent (GPUTemperatureHigh, LoldayGPUVRAMHigh)
#   3. all 4 new rules are present with the right severity
#   4. PodCrashLoopBackOff has for: 15m and severity: warning
#   5. TrivyCriticalCVE severity is now warning (was critical)
#   6. AlertmanagerConfig has 5 inhibitRules + 2 receivers + per-route repeatInterval
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

# --- PrometheusRule inventory ---

yq eval-all '
  select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/pr.yaml"

ALERT_NAMES="$(yq eval '.spec.groups[].rules[].alert' "$TMPDIR/pr.yaml" | sort -u)"
ALERT_COUNT="$(echo "$ALERT_NAMES" | wc -l | tr -d ' ')"

[ "$ALERT_COUNT" = "16" ] \
  || fail "expected 16 alerts in lolday-baseline; got $ALERT_COUNT.  Names:
$ALERT_NAMES"
pass "16 alerts present"

# Removed rules must be absent
for removed in GPUTemperatureHigh LoldayGPUVRAMHigh; do
  if echo "$ALERT_NAMES" | grep -qx "$removed"; then
    fail "removed alert '$removed' is still present in chart"
  fi
done
pass "GPUTemperatureHigh + LoldayGPUVRAMHigh removed"

# New rules must be present
for new in DCGMXIDError DCGMThrottleReasonsPersistent GpuSignalFailSafeStuck GpuSignalCountMismatch; do
  echo "$ALERT_NAMES" | grep -qx "$new" \
    || fail "new alert '$new' missing from chart"
done
pass "4 new alerts present (DCGMXIDError, DCGMThrottleReasonsPersistent, GpuSignalFailSafeStuck, GpuSignalCountMismatch)"

# Per-rule severity assertions
get_severity() {
  yq eval ".spec.groups[].rules[] | select(.alert == \"$1\") | .labels.severity" "$TMPDIR/pr.yaml"
}
get_for() {
  yq eval ".spec.groups[].rules[] | select(.alert == \"$1\") | .for" "$TMPDIR/pr.yaml"
}

[ "$(get_severity DCGMXIDError)" = "critical" ] || fail "DCGMXIDError severity must be critical"
[ "$(get_severity DCGMThrottleReasonsPersistent)" = "warning" ] || fail "DCGMThrottleReasonsPersistent severity must be warning"
[ "$(get_severity GpuSignalFailSafeStuck)" = "warning" ] || fail "GpuSignalFailSafeStuck severity must be warning"
[ "$(get_severity GpuSignalCountMismatch)" = "warning" ] || fail "GpuSignalCountMismatch severity must be warning"
[ "$(get_severity TrivyCriticalCVE)" = "warning" ] || fail "TrivyCriticalCVE severity must be warning (demoted)"
[ "$(get_severity PodCrashLoopBackOff)" = "warning" ] || fail "PodCrashLoopBackOff severity must be warning"
[ "$(get_for PodCrashLoopBackOff)" = "15m" ] || fail "PodCrashLoopBackOff for: must be 15m"
pass "severity + for hysteresis correct on key rules"

# --- AlertmanagerConfig ---

yq eval-all '
  select(.kind == "AlertmanagerConfig" and .metadata.name == "discord-receivers")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/amc.yaml"

INHIBIT_COUNT="$(yq eval '.spec.inhibitRules | length' "$TMPDIR/amc.yaml")"
[ "$INHIBIT_COUNT" = "5" ] \
  || fail "expected 5 inhibitRules; got $INHIBIT_COUNT"
pass "5 inhibitRules present"

RECV_COUNT="$(yq eval '.spec.receivers | length' "$TMPDIR/amc.yaml")"
[ "$RECV_COUNT" = "2" ] || fail "expected 2 receivers; got $RECV_COUNT"
pass "2 receivers (discord-critical, discord-warning)"

CRIT_INTERVAL="$(yq eval '.spec.route.routes[0].repeatInterval' "$TMPDIR/amc.yaml")"
WARN_INTERVAL="$(yq eval '.spec.route.routes[1].repeatInterval' "$TMPDIR/amc.yaml")"
[ "$CRIT_INTERVAL" = "4h" ] || fail "critical route repeatInterval must be 4h (got $CRIT_INTERVAL)"
[ "$WARN_INTERVAL" = "24h" ] || fail "warning route repeatInterval must be 24h (got $WARN_INTERVAL)"
pass "per-route repeatIntervals: critical 4h, warning 24h"

CRIT_CONTENT="$(yq eval '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].content // ""' "$TMPDIR/amc.yaml")"
[ "$CRIT_CONTENT" = "@here" ] || fail "discord-critical must use content: @here (got '$CRIT_CONTENT')"
WARN_CONTENT="$(yq eval '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].content // ""' "$TMPDIR/amc.yaml")"
[ -z "$WARN_CONTENT" ] || fail "discord-warning must NOT set content (no @here ping); got '$WARN_CONTENT'"
pass "@here ping policy: critical only"

# Note: `amtool config check` was considered as drift insurance per Plan
# §Task 4 step 3 ("Optional but recommended"), but its parser expects
# native `alertmanager.yml` syntax — not the AlertmanagerConfig CRD that
# Prometheus Operator translates at runtime. The yq+helm assertions above
# already verify the CRD's structural invariants; native-config validation
# happens inside the prom-operator alertmanager-config-reloader sidecar
# at deploy time. No amtool wiring here.

echo ""
echo "All assertions passed."
