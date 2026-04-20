#!/usr/bin/env bash
# Phase 7.1 — Alertmanager Discord receiver contract test (Path B: AlertmanagerConfig CRD).
#
# Prometheus Operator v0.86.2 does not yet mirror Alertmanager v0.28+ `webhook_url_file`
# onto discord_configs in its inline-config validator. We use the operator-native
# AlertmanagerConfig CRD (v1alpha1) with `apiURL.name/key` SecretKeySelector, which is
# the idiom the operator was designed for. URLs live in the `alertmanager-discord`
# Secret only (created by scripts/deploy.sh), never in helm release state or git.
#
# Verifies that charts/lolday renders:
#   1. An AlertmanagerConfig CR `discord-receivers` in the monitoring ns with the
#      selector label `lolday-alertmanager-config=discord`, two receivers
#      (discord-critical, discord-warning) each referencing the shared Secret, and
#      severity-based routes.
#   2. The Alertmanager CR carries matching `alertmanagerConfigSelector` and
#      `alertmanagerConfigMatcherStrategy.type: None` so the AC CR is picked up
#      and its matchers are not auto-wrapped in a namespace filter.
#   3. The kps-generated inline alertmanager.yaml Secret is minimal (only 'null'
#      receiver, no discord_configs — those live in the AC CR).
#   4. The minimal inline config still passes `amtool check-config`.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CHART="$REPO_ROOT/charts/lolday"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "✗ FAIL: $*" >&2; exit 1; }
pass() { echo "✓ $*"; }

for bin in helm yq base64 amtool; do
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

# --- Step 2: extract AlertmanagerConfig CR 'discord-receivers' ---
yq eval-all '
  select(.kind == "AlertmanagerConfig" and .metadata.name == "discord-receivers")
' "$TMPDIR/rendered.yaml" > "$TMPDIR/ac.yaml"
[ -s "$TMPDIR/ac.yaml" ] \
  || fail "AlertmanagerConfig 'discord-receivers' not rendered — expected at charts/lolday/templates/monitoring/alertmanager-config-discord.yaml"
pass "AlertmanagerConfig 'discord-receivers' rendered"

assert_eq() {
  local expr="$1" expected="$2" msg="$3" file="${4:-$TMPDIR/ac.yaml}"
  local got
  got="$(yq eval "$expr" "$file")"
  [ "$got" = "$expected" ] || fail "$msg (got: '$got', expected: '$expected')"
}

# --- Step 3: AC CR must be labeled to match Alertmanager CR's selector, in monitoring ns ---
assert_eq '.metadata.labels."lolday-alertmanager-config"' 'discord' \
  "AC CR must have label lolday-alertmanager-config=discord (selector match)"
assert_eq '.metadata.namespace' 'monitoring' \
  "AC CR must be in monitoring ns (same as Alertmanager pod)"
pass "AC CR labeled + in monitoring ns"

# --- Step 4: receivers — exactly [discord-critical, discord-warning] ---
assert_eq '[.spec.receivers[] | .name] | sort | .[]' $'discord-critical\ndiscord-warning' \
  "AC CR receivers must be exactly [discord-critical, discord-warning]"
pass "AC CR has discord-critical + discord-warning"

# Discord-critical: apiURL Secret ref + @here + sendResolved
assert_eq '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].apiURL.name' \
  'alertmanager-discord' \
  "discord-critical apiURL.name must point to 'alertmanager-discord' Secret"
assert_eq '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].apiURL.key' \
  'webhook-url-critical' \
  "discord-critical apiURL.key must be 'webhook-url-critical'"
assert_eq '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].content' \
  '@here' \
  "discord-critical content must be '@here' (Discord mention push — root value prop of the Path B refactor)"
assert_eq '.spec.receivers[] | select(.name == "discord-critical") | .discordConfigs[0].sendResolved' \
  'true' \
  "discord-critical sendResolved must be true"
pass "discord-critical: apiURL Secret ref + @here + sendResolved=true"

# Discord-warning: apiURL Secret ref + sendResolved + NO @here
assert_eq '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].apiURL.name' \
  'alertmanager-discord' \
  "discord-warning apiURL.name must point to 'alertmanager-discord' Secret"
assert_eq '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].apiURL.key' \
  'webhook-url-warning' \
  "discord-warning apiURL.key must be 'webhook-url-warning'"
assert_eq '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].sendResolved' \
  'true' \
  "discord-warning sendResolved must be true"

warn_content="$(yq eval '.spec.receivers[] | select(.name == "discord-warning") | .discordConfigs[0].content // ""' "$TMPDIR/ac.yaml")"
[[ "$warn_content" != *"@here"* ]] \
  || fail "discord-warning must NOT include @here (only critical should ping)"
pass "discord-warning: apiURL Secret ref + sendResolved=true + no @here"

# --- Step 5: route sub-routes dispatch by severity label ---
assert_eq '.spec.route.routes | length' '2' \
  "AC CR route.routes must have exactly 2 sub-routes (critical + warning)"

assert_eq '.spec.route.routes[] | select(.receiver == "discord-critical") | .matchers[0].name' \
  'severity' \
  "critical sub-route matcher[0].name must be 'severity'"
assert_eq '.spec.route.routes[] | select(.receiver == "discord-critical") | .matchers[0].value' \
  'critical' \
  "critical sub-route matcher[0].value must be 'critical'"
assert_eq '.spec.route.routes[] | select(.receiver == "discord-critical") | .matchers[0].matchType' \
  '=' \
  "critical sub-route matcher[0].matchType must be '='"
pass "critical sub-route: severity=critical → discord-critical"

assert_eq '.spec.route.routes[] | select(.receiver == "discord-warning") | .matchers[0].name' \
  'severity' \
  "warning sub-route matcher[0].name must be 'severity'"
assert_eq '.spec.route.routes[] | select(.receiver == "discord-warning") | .matchers[0].value' \
  'warning' \
  "warning sub-route matcher[0].value must be 'warning'"
assert_eq '.spec.route.routes[] | select(.receiver == "discord-warning") | .matchers[0].matchType' \
  '=' \
  "warning sub-route matcher[0].matchType must be '='"
pass "warning sub-route: severity=warning → discord-warning"

# --- Step 6: Alertmanager CR has matching selector + matcherStrategy ---
yq eval-all 'select(.kind == "Alertmanager")' "$TMPDIR/rendered.yaml" > "$TMPDIR/am_cr.yaml"
[ -s "$TMPDIR/am_cr.yaml" ] || fail "Alertmanager CR not rendered"

assert_eq '.spec.alertmanagerConfigSelector.matchLabels."lolday-alertmanager-config"' \
  'discord' \
  "Alertmanager CR must select AC CRs with label lolday-alertmanager-config=discord" \
  "$TMPDIR/am_cr.yaml"
assert_eq '.spec.alertmanagerConfigMatcherStrategy.type' \
  'None' \
  "alertmanagerConfigMatcherStrategy.type must be 'None' (so AC CR sub-route matchers are not wrapped in ns filter)" \
  "$TMPDIR/am_cr.yaml"
pass "Alertmanager CR: configSelector + matcherStrategy=None"

# --- Step 7: inline kps-generated alertmanager.yaml is minimal (no Discord inline) ---
am_b64="$(
  yq eval-all '
    select(.kind == "Secret" and .data."alertmanager.yaml" != null)
    | .data."alertmanager.yaml"
  ' "$TMPDIR/rendered.yaml"
)"
[ -n "$am_b64" ] || fail "kps Alertmanager Secret not rendered"
echo "$am_b64" | base64 -d > "$TMPDIR/inline.yaml"

inline_receivers="$(yq eval '[.receivers[] | .name] | sort | .[]' "$TMPDIR/inline.yaml")"
[ "$inline_receivers" = "null" ] \
  || fail "inline config should only have 'null' receiver (discord moved to AC CR), got: '$inline_receivers'"
pass "inline config receivers = ['null'] (Discord lives in AC CR, not inline)"

if grep -q "discord_configs:" "$TMPDIR/inline.yaml"; then
  fail "inline config still contains discord_configs — should live only in AC CR"
fi
pass "no discord_configs in inline config"

# --- Step 8: minimal inline config still passes amtool ---
if amtool check-config "$TMPDIR/inline.yaml" >"$TMPDIR/amtool.out" 2>&1; then
  pass "amtool check-config passed on minimal inline config"
else
  cat "$TMPDIR/amtool.out" >&2
  fail "amtool check-config failed"
fi

echo ""
echo "All assertions passed."
