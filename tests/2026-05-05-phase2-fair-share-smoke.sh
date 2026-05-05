#!/usr/bin/env bash
# Smoke: Phase 2 — Volcano per-user queue + capability cap.
#
# Spec: docs/superpowers/specs/2026-05-05-gpu-scheduling-and-oom-defense-design.md §7 Phase 2
set -euo pipefail

NS_INFRA=${NS_INFRA:-lolday}
fail=0

echo "[step 1/4] lolday-training queue has capability cap"
cap=$(kubectl get queues.scheduling.volcano.sh lolday-training -o jsonpath='{.spec.capability}' 2>/dev/null)
if [ -n "${cap}" ] && echo "${cap}" | grep -q "nvidia.com/gpu"; then
  echo "OK: ${cap}"
else
  echo "FAIL: lolday-training has no capability"
  fail=1
fi

echo ""
echo "[step 2/4] scheduler config has drf + proportion plugins"
plugins=$(kubectl -n "${NS_INFRA}" get cm lolday-scheduler-configmap -o jsonpath='{.data.volcano-scheduler\.conf}' 2>/dev/null)
if echo "${plugins}" | grep -q "name: drf" && echo "${plugins}" | grep -q "name: proportion"; then
  echo "OK"
else
  echo "FAIL: drf or proportion plugin missing in scheduler config"
  fail=1
fi

echo ""
echo "[step 3/4] backend SA can create cluster-scoped Queues"
# `kubectl auth can-i` for a cluster-scoped CRD prints a Warning to stderr
# ('resource is not namespace scoped') but the answer 'yes'/'no' still lands
# on stdout's last line. Normal exit codes are 0 (yes) / 1 (no).
if kubectl auth can-i create queues.scheduling.volcano.sh \
     --as="system:serviceaccount:${NS_INFRA}:backend" 2>/dev/null; then
  echo "OK"
else
  echo "FAIL: backend SA cannot create cluster-scoped Queues"
  fail=1
fi

echo ""
echo "[step 4/4] no orphan user-queues from earlier test runs (informational)"
orphans=$(kubectl get queues.scheduling.volcano.sh -l lolday.io/role=user-queue \
  --no-headers 2>/dev/null | wc -l)
echo "INFO: ${orphans} per-user queue(s) currently in cluster"

echo ""
if [ "${fail}" -eq 0 ]; then
  echo "=== SMOKE PASSED ==="
else
  echo "=== SMOKE FAILED ==="
  exit 1
fi
