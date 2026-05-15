#!/usr/bin/env bash
# D2.5 Task 17 — PSS rejects a privileged Pod when the namespace label is
# promoted from enforce=baseline to enforce=restricted.
#
# The chart ships lolday-jobs at enforce=baseline (architecture.md §10
# tech-debt: pending promotion per docs/runbooks/pss-label-promotion.md).
# This test labels the namespace enforce=restricted, applies a privileged
# Pod, asserts rejection by PodSecurity, then restores baseline. Trap
# unwinds the label + cleans the test Pod on any exit.
set -euo pipefail

NS=${TEST_NS:-lolday-jobs}

if ! kubectl get namespace "$NS" >/dev/null 2>&1; then
  echo "::warning::Namespace $NS not found in cluster; skipping"
  exit 0
fi

restore_label() {
  kubectl label namespace "$NS" \
    pod-security.kubernetes.io/enforce=baseline --overwrite >/dev/null 2>&1 || true
  kubectl delete pod -n "$NS" e2e-pss-privileged-test --ignore-not-found >/dev/null 2>&1 || true
}
trap restore_label EXIT

echo "::group::Promote $NS to enforce=restricted"
kubectl label namespace "$NS" \
  pod-security.kubernetes.io/enforce=restricted --overwrite
kubectl get namespace "$NS" \
  -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' \
  | grep -q restricted
echo "::endgroup::"

echo "::group::Apply a privileged Pod (expect rejection)"
set +e
kubectl -n "$NS" apply -f - 2>&1 <<'YAML' | tee /tmp/pss-apply.log
apiVersion: v1
kind: Pod
metadata:
  name: e2e-pss-privileged-test
spec:
  containers:
    - name: c
      image: alpine:3.20
      securityContext:
        privileged: true
        allowPrivilegeEscalation: true
        capabilities:
          add: ["SYS_ADMIN"]
  restartPolicy: Never
YAML
rc=$?
set -e
echo "::endgroup::"

if [ "$rc" -eq 0 ]; then
  echo "::error::Privileged Pod admission succeeded under enforce=restricted"
  exit 1
fi

if ! grep -qE 'PodSecurity|restricted|privileged' /tmp/pss-apply.log; then
  echo "::error::Pod was rejected but not by the PSS restricted profile"
  cat /tmp/pss-apply.log
  exit 1
fi

echo "PASS: PSS restricted profile rejected the privileged Pod as expected"
