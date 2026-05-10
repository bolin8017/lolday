#!/usr/bin/env bash
# Live smoke for host-aware GPU signal (spec 2026-05-10).
#
# Pre-reqs:
#   - lolday backend deployed (helm upgrade succeeded)
#   - kubectl context points at the same cluster
#   - $LOLDAY_ADMIN_TOKEN exported (Bearer token w/ admin role)
#   - $LOLDAY_API_BASE exported (e.g. https://lolday.connlabai.com/api/v1)
#   - python3 + torch on the host (`python3 -c 'import torch'` works); if
#     not, replace TestB with a different GPU-touching command (e.g.
#     `cuda-samples deviceQuery`).
#
# Tests A-D from spec §8.3.

set -euo pipefail

API="${LOLDAY_API_BASE:?need LOLDAY_API_BASE}"
TOKEN="${LOLDAY_ADMIN_TOKEN:?need LOLDAY_ADMIN_TOKEN}"
H="-H 'Authorization: Bearer $TOKEN'"

curl_status() {
  curl -s -H "Authorization: Bearer $TOKEN" "$API/cluster/gpu-status"
}

free_count() {
  curl_status | python3 -c 'import sys,json; print(json.load(sys.stdin)["free_count"])'
}

fail_safe() {
  curl_status | python3 -c 'import sys,json; print(json.load(sys.stdin)["fail_safe_active"])'
}

external_count() {
  curl_status | python3 -c 'import sys,json; print(json.load(sys.stdin)["in_use_by_external"])'
}

echo "=== Test A: cluster all-free ==="
[[ "$(free_count)" == "2" ]] || { echo "FAIL: expected free_count=2"; exit 1; }
echo "PASS"

echo "=== Test B: host-level GPU usage ==="
echo "Spawning host-level CUDA process for 120s..."
python3 -c '
import torch, time
x = torch.zeros(int(1e8)).cuda()  # ~400MB+ VRAM
print("VRAM allocated; sleeping 120s")
time.sleep(120)
' &
PYPID=$!
trap "kill $PYPID 2>/dev/null || true" EXIT
sleep 30  # let DCGM scrape pick up

if [[ "$(external_count)" -lt "1" ]]; then
  echo "FAIL: expected in_use_by_external >= 1 within 30s"
  exit 1
fi
if [[ "$(free_count)" != "1" && "$(free_count)" != "0" ]]; then
  echo "FAIL: expected free_count <= 1 (got $(free_count))"
  exit 1
fi
echo "PASS — external_count=$(external_count), free_count=$(free_count)"

echo "=== Test C: kill host process, expect recovery ==="
kill $PYPID
trap - EXIT
sleep 60  # DCGM scrape (15s) + Prom resolution + cache TTL (10s) margin
[[ "$(free_count)" == "2" ]] || { echo "FAIL: free_count did not recover (got $(free_count))"; exit 1; }
echo "PASS"

echo "=== Test D: simulated Prom outage ==="
# Kube-prometheus-stack may install the StatefulSet under different names
# depending on chart version / release name. Discover at runtime.
PROM_STS="$(kubectl -n monitoring get statefulset \
  -l app.kubernetes.io/name=prometheus \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
[ -n "$PROM_STS" ] || { echo "FAIL: cannot find prometheus StatefulSet in monitoring ns"; exit 1; }
echo "  Using StatefulSet: $PROM_STS"
kubectl -n monitoring scale --replicas=0 "statefulset/$PROM_STS"
sleep 30
[[ "$(fail_safe)" == "True" ]] || { echo "FAIL: expected fail_safe_active=True"; kubectl -n monitoring scale --replicas=1 "statefulset/$PROM_STS"; exit 1; }
echo "PASS — restoring Prom"
kubectl -n monitoring scale --replicas=1 "statefulset/$PROM_STS"
echo "Waiting for Prom to come back..."
kubectl -n monitoring wait --for=condition=Ready pod -l app.kubernetes.io/name=prometheus --timeout=120s
sleep 30
[[ "$(fail_safe)" == "False" ]] || { echo "FAIL: fail-safe did not clear after Prom recovery"; exit 1; }
echo "All smoke tests PASS"
