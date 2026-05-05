#!/usr/bin/env bash
# Smoke: verify Phase 0 kubelet args landed.
# Run manually post-apply (no automation).
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7
set -euo pipefail

NODE=${NODE:-server30}
fail=0

echo "[step 1/4] kubeletconfig has kubeReserved/systemReserved set"
kubectl get --raw "/api/v1/nodes/${NODE}/proxy/configz" \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)["kubeletconfig"]
errs = []
kr = d.get("kubeReserved") or {}
sr = d.get("systemReserved") or {}
eh = d.get("evictionHard") or {}
es = d.get("evictionSoft") or {}
if kr.get("memory") != "2Gi":
    errs.append(f"kubeReserved.memory expected 2Gi, got {kr}")
if sr.get("memory") != "4Gi":
    errs.append(f"systemReserved.memory expected 4Gi, got {sr}")
if eh.get("memory.available") != "1Gi":
    errs.append(f"evictionHard.memory.available expected 1Gi, got {eh}")
if es.get("memory.available") != "2Gi":
    errs.append(f"evictionSoft.memory.available expected 2Gi, got {es}")
if errs:
    print("FAIL:")
    for e in errs:
        print(" -", e)
    sys.exit(1)
print("OK")
' || fail=1

echo ""
echo "[step 2/4] node Allocatable shrunk vs Capacity"
delta=$(kubectl get node "${NODE}" -o json | python3 -c '
import json, sys
n = json.load(sys.stdin)
def parse_ki(s):
    s = str(s)
    if s.endswith("Ki"): return int(s[:-2])
    return int(s)
cap = parse_ki(n["status"]["capacity"]["memory"])
alloc = parse_ki(n["status"]["allocatable"]["memory"])
delta_gi = (cap - alloc) / 1024 / 1024
print(f"{delta_gi:.2f}")
')
# Expected delta: kube=2Gi + system=4Gi + eviction-hard=1Gi = ~7Gi
case "${delta}" in
  6.*|7.*) echo "OK: delta = ${delta} GiB (in expected 6-8 range)" ;;
  *) echo "FAIL: Capacity-Allocatable delta ${delta} GiB; expected ~7 GiB"; fail=1 ;;
esac

echo ""
echo "[step 3/4] systemd drop-in present"
if [ -f /etc/systemd/system/k3s.service.d/10-lolday-kubelet-args.conf ]; then
  echo "OK"
else
  echo "FAIL: drop-in file missing"
  fail=1
fi

echo ""
echo "[step 4/4] no NodeMemoryPressure right now"
mp=$(kubectl get node "${NODE}" -o json | python3 -c '
import json, sys
for c in json.load(sys.stdin)["status"]["conditions"]:
    if c["type"] == "MemoryPressure":
        print(c["status"])
        break
')
if [ "${mp}" = "False" ]; then
  echo "OK: MemoryPressure=False"
else
  echo "FAIL: MemoryPressure=${mp}"
  fail=1
fi

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
