#!/usr/bin/env bash
# Phase 7.1 — deploy.sh Discord Secret management contract test.
#
# Verifies scripts/deploy.sh gates on DISCORD_WEBHOOK_URL_CRITICAL/_WARNING
# and creates the alertmanager-discord Secret in the monitoring namespace
# idempotently before helm upgrade. Script-text level assertions (behavior
# end-to-end is covered by the e2e smoke test post-deploy).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DEPLOY="$REPO_ROOT/scripts/deploy.sh"

fail() { echo "✗ FAIL: $*" >&2; exit 1; }
pass() { echo "✓ $*"; }

[ -f "$DEPLOY" ] || fail "$DEPLOY not found"

# --- 1. required env var checks using ${VAR:?} ---
grep -qE '\$\{DISCORD_WEBHOOK_URL_CRITICAL:\?' "$DEPLOY" \
  || fail "DISCORD_WEBHOOK_URL_CRITICAL is not a required env var (missing \${VAR:?} gate)"
pass "DISCORD_WEBHOOK_URL_CRITICAL required"

grep -qE '\$\{DISCORD_WEBHOOK_URL_WARNING:\?' "$DEPLOY" \
  || fail "DISCORD_WEBHOOK_URL_WARNING is not a required env var (missing \${VAR:?} gate)"
pass "DISCORD_WEBHOOK_URL_WARNING required"

# --- 2. Secret create command present, idempotent, in monitoring ns ---
# The command must:
#   - Use kubectl create secret generic alertmanager-discord
#   - Target the monitoring namespace (AM pod's ns)
#   - Pipe through --dry-run=client | kubectl apply (idempotent upsert pattern)
#   - Include both webhook URL literals
grep -q "kubectl.*-n\s*monitoring.*create secret generic alertmanager-discord" "$DEPLOY" \
  || grep -qE "kubectl\s+.*\s+-n\s+monitoring\s+create\s+secret\s+generic\s+alertmanager-discord" "$DEPLOY" \
  || fail "kubectl create secret 'alertmanager-discord' in monitoring ns not found in deploy.sh"
pass "Secret create command references alertmanager-discord in monitoring ns"

grep -qE "from-literal=webhook-url-critical=" "$DEPLOY" \
  || fail "Secret create missing --from-literal=webhook-url-critical=..."
grep -qE "from-literal=webhook-url-warning=" "$DEPLOY" \
  || fail "Secret create missing --from-literal=webhook-url-warning=..."
pass "Secret includes both webhook-url-critical and webhook-url-warning"

grep -qE "dry-run=client.*\|.*kubectl apply" "$DEPLOY" \
  || fail "Secret create should pipe through 'kubectl ... --dry-run=client -o yaml | kubectl apply -f -' for idempotency"
pass "Secret create is idempotent (dry-run piped to apply)"

# --- 3. Secret create must run BEFORE helm upgrade (so mount references resolve) ---
# Line of 'alertmanager-discord' creation < line of 'helm upgrade'
secret_ln=$(grep -nE "create secret generic alertmanager-discord" "$DEPLOY" | head -1 | cut -d: -f1)
helm_ln=$(grep -nE "^helm upgrade" "$DEPLOY" | head -1 | cut -d: -f1)
[ -n "$secret_ln" ] && [ -n "$helm_ln" ] && [ "$secret_ln" -lt "$helm_ln" ] \
  || fail "Secret create ($secret_ln) must precede helm upgrade ($helm_ln)"
pass "Secret create runs before helm upgrade (line $secret_ln < $helm_ln)"

# --- 4. shell syntax valid ---
bash -n "$DEPLOY" || fail "deploy.sh has bash syntax errors"
pass "deploy.sh syntax valid"

echo ""
echo "All assertions passed."
