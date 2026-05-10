#!/usr/bin/env bash
# Live smoke for 議題 B (alerting redesign).
# Operator runs after `bash scripts/deploy.sh` has rolled out the new chart
# AND the operator has updated DISCORD_WEBHOOK_URL_WARNING in
# ~/.lolday-secrets.env to point at the new Spidey Warnings channel.
#
# Pre-reqs:
#   - kubectl context points at server30
#   - amtool is on PATH (ships with prometheus toolchain)
#   - port-forward to alertmanager available, or amtool URL configured
set -euo pipefail

# Allow either env-var-driven or port-forward driven access.
AM_URL="${ALERTMANAGER_URL:-http://localhost:9093}"

require() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
require amtool
require kubectl

cleanup() {
  echo "==> cleanup: silence all test alerts"
  amtool --alertmanager.url="$AM_URL" silence query --within=1h --silenced=false 2>/dev/null \
    | awk '/test-alert-/ {print $1}' \
    | xargs -r -n1 amtool --alertmanager.url="$AM_URL" silence expire || true
}
trap cleanup EXIT

echo "==> Test A: critical alert routes to Captain Hook with @here"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayCoreServiceDown" severity="critical" job="backend" \
  annotation:summary="smoke test A — please ignore"
sleep 35  # group_wait + small margin
echo "  Inspect Captain Hook channel for: 🚨 [CRITICAL] LoldayCoreServiceDown + @here"
echo "  Press Enter when confirmed."
read -r

echo "==> Test B: warning alert routes to Spidey Warnings without @here"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="PodCrashLoopBackOff" severity="warning" \
  namespace="lolday" pod="smoke-test-pod" container="x" reason="CrashLoopBackOff" \
  annotation:summary="smoke test B — please ignore"
sleep 35
echo "  Inspect Spidey Warnings channel for: ⚠️ [WARNING] PodCrashLoopBackOff (NO @here)"
echo "  Press Enter when confirmed."
read -r

echo "==> Test C: inhibition — backend down + error rate elevated"
echo "  Adding source alert (LoldayCoreServiceDown)…"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayCoreServiceDown" severity="critical" job="backend" \
  annotation:summary="smoke test C source — please ignore"
sleep 5
echo "  Adding target alert (LoldayBackendErrorRateElevated)…"
amtool --alertmanager.url="$AM_URL" alert add \
  alertname="LoldayBackendErrorRateElevated" severity="warning" stage="dispatch" \
  annotation:summary="smoke test C target — please ignore"
sleep 35
echo "  Inspect Spidey Warnings: target alert should NOT appear (inhibited)."
echo "  Press Enter when confirmed."
read -r

echo "==> Test D: GpuSignalFailSafeStuck end-to-end"
echo "  This requires Prometheus actually unreachable for 30+ min."
echo "  To simulate quickly:"
echo "    PROM_STS=\$(kubectl -n monitoring get statefulset \\"
echo "      -l app.kubernetes.io/name=prometheus -o jsonpath='{.items[0].metadata.name}')"
echo "    kubectl -n monitoring scale --replicas=0 \"statefulset/\$PROM_STS\""
echo "  Wait 31 minutes (or skip this test — it overlaps with 議題 A's smoke Test D)."
echo "  Press Enter to skip, or wait + observe in Spidey Warnings."
read -r

echo ""
echo "All interactive tests prompted. Cleanup runs automatically."
