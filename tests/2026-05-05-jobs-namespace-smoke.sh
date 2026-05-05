#!/usr/bin/env bash
# Smoke: Phase 1 — lolday-jobs namespace migration landed correctly.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7 Phase 1
set -euo pipefail

NS_INFRA=${NS_INFRA:-lolday}
NS_JOBS=${NS_JOBS:-lolday-jobs}
fail=0

echo "[step 1/6] new namespace exists"
if kubectl get ns "${NS_JOBS}" >/dev/null 2>&1; then
  echo "OK"
else
  echo "FAIL: ${NS_JOBS} missing"
  fail=1
fi

echo ""
echo "[step 2/6] ResourceQuota in lolday-jobs"
kubectl -n "${NS_JOBS}" get resourcequota lolday-jobs-quota -o jsonpath='{.spec.hard}' 2>/dev/null \
  | python3 -c '
import sys, json
data = sys.stdin.read().strip()
if not data:
    print("FAIL: lolday-jobs-quota missing"); sys.exit(1)
d = json.loads(data)
errs = []
expected = {
    "requests.memory": "30Gi",
    "limits.memory": "50Gi",
    "requests.nvidia.com/gpu": "2",
    "count/pods": "16",
}
for k, v in expected.items():
    if d.get(k) != v:
        errs.append(f"{k}={d.get(k)} expected {v}")
if errs:
    print("FAIL:", "; ".join(errs)); sys.exit(1)
print("OK")
' || fail=1

echo ""
echo "[step 3/6] LimitRange in lolday-jobs"
kubectl -n "${NS_JOBS}" get limitrange lolday-jobs-limits -o jsonpath='{.spec.limits[0].max}' 2>/dev/null \
  | python3 -c '
import sys, json
data = sys.stdin.read().strip()
if not data:
    print("FAIL: lolday-jobs-limits missing"); sys.exit(1)
d = json.loads(data)
if d.get("memory") == "16Gi" and d.get("cpu") == "4":
    print("OK")
else:
    print(f"FAIL: max={d}"); sys.exit(1)
' || fail=1

echo ""
echo "[step 4/6] ResourceQuota in lolday infra"
if kubectl -n "${NS_INFRA}" get resourcequota lolday-infra-quota >/dev/null 2>&1; then
  echo "OK"
else
  echo "FAIL: lolday-infra-quota missing"
  fail=1
fi

echo ""
echo "[step 5/6] backend SA can manage vcjobs in lolday-jobs"
out=$(kubectl auth can-i create jobs.batch.volcano.sh -n "${NS_JOBS}" \
  --as="system:serviceaccount:${NS_INFRA}:backend" 2>&1 || true)
case "${out}" in
  yes) echo "OK" ;;
  *) echo "FAIL: backend SA cannot create vcjobs in ${NS_JOBS}: ${out}"; fail=1 ;;
esac

echo ""
echo "[step 6/6] backend env JOB_NAMESPACE points to lolday-jobs"
ns=$(kubectl -n "${NS_INFRA}" get deploy backend \
  -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="JOB_NAMESPACE")].value}' 2>/dev/null)
if [ "${ns}" = "${NS_JOBS}" ]; then
  echo "OK"
else
  echo "FAIL: JOB_NAMESPACE=${ns}, expected ${NS_JOBS}"
  fail=1
fi

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
