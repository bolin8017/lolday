#!/usr/bin/env bash
# Phase 7.2 — TrivyCriticalCVE PrometheusRule contract test.
#
# Asserts the chart ships a rule that fires on CRITICAL-severity CVEs detected
# by Trivy Operator and routes it through Phase 7.1's Discord pipeline
# (severity=critical label → AlertmanagerConfig CR → #lolday-alerts-critical
# with @here push).
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

# Our custom rules live on PrometheusRule 'lolday-baseline'.
yq eval-all '
  select(.kind == "PrometheusRule" and .metadata.name == "lolday-baseline")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/pr.yaml"
[ -s "$TMPDIR/pr.yaml" ] || fail "PrometheusRule 'lolday-baseline' not rendered (pre-existing fixture)"

# Pull the TrivyCriticalCVE alert out of whatever group it lives in.
yq eval '
  .spec.groups[] | .rules[] | select(.alert == "TrivyCriticalCVE")
' "$TMPDIR/pr.yaml" > "$TMPDIR/rule.yaml"
[ -s "$TMPDIR/rule.yaml" ] \
  || fail "rule 'TrivyCriticalCVE' not found — expected under a group in charts/lolday/templates/monitoring/alertmanager-rules.yaml"
pass "TrivyCriticalCVE rule rendered"

severity="$(yq eval '.labels.severity' "$TMPDIR/rule.yaml")"
[ "$severity" = "critical" ] \
  || fail "labels.severity must be 'critical' to route through Phase 7.1's Discord critical channel (got: '$severity')"
pass "labels.severity: critical"

# expr shape: must filter on the metric Trivy emits + severity=\"Critical\"
# (title-case as Trivy uses). We don't pin exact aggregation because count()
# vs sum() by various label sets are all reasonable expressions.
expr_body="$(yq eval '.expr' "$TMPDIR/rule.yaml")"
echo "$expr_body" | grep -qE 'trivy_image_vulnerabilities' \
  || fail "expr must reference the trivy_image_vulnerabilities metric (got: '$expr_body')"
echo "$expr_body" | grep -qE 'severity="Critical"' \
  || fail "expr must filter severity=\"Critical\" (title-case — Trivy emits capitalized severities)"
pass "expr references trivy_image_vulnerabilities{severity=\"Critical\"}"

# for: <duration> — rules without hysteresis would flap on scan-cycle boundaries
for_duration="$(yq eval '.for' "$TMPDIR/rule.yaml")"
[ -n "$for_duration" ] && [ "$for_duration" != "null" ] \
  || fail "rule must set 'for' (hysteresis); otherwise one scan miss toggles the alert"
pass "hysteresis: for=$for_duration"

summary="$(yq eval '.annotations.summary // ""' "$TMPDIR/rule.yaml")"
[ -n "$summary" ] || fail "annotations.summary required"
description="$(yq eval '.annotations.description // ""' "$TMPDIR/rule.yaml")"
[ -n "$description" ] || fail "annotations.description should explain remediation"
pass "annotations: summary + description present"

echo ""
echo "All assertions passed."
