#!/usr/bin/env bash
# D2.5 Task 16 — Kyverno rejects an unsigned Harbor image when the
# verify-lolday-harbor-image-signatures ClusterPolicy is in Enforce mode.
#
# The chart ships the policy at validationFailureAction=Audit (matches
# operator runbook docs/runbooks/kyverno-harbor-signing.md). This test
# patches it to Enforce, applies a Pod referencing an unsigned image
# from harbor.lolday.svc, asserts admission is rejected, then restores
# Audit. Trap unwinds the patch + cleans the test Pod on any exit.
#
# Run from repo root: bash tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh
set -euo pipefail

NS=${TEST_NS:-default}
POLICY=verify-lolday-harbor-image-signatures

if ! kubectl get clusterpolicy "$POLICY" >/dev/null 2>&1; then
  echo "::warning::ClusterPolicy $POLICY not found in cluster; skipping (chart did not include Kyverno bootstrap)"
  exit 0
fi

restore_policy() {
  kubectl patch clusterpolicy "$POLICY" --type=json \
    -p='[{"op":"replace","path":"/spec/validationFailureAction","value":"Audit"}]' \
    >/dev/null 2>&1 || true
  kubectl delete pod -n "$NS" e2e-unsigned-image-test --ignore-not-found >/dev/null 2>&1 || true
}
trap restore_policy EXIT

echo "::group::Flip $POLICY to Enforce"
kubectl patch clusterpolicy "$POLICY" --type=json \
  -p='[{"op":"replace","path":"/spec/validationFailureAction","value":"Enforce"}]'
kubectl get clusterpolicy "$POLICY" \
  -o jsonpath='{.spec.validationFailureAction}' | grep -q Enforce
echo "::endgroup::"

echo "::group::Apply a Pod with an unsigned Harbor image (expect rejection)"
set +e
kubectl -n "$NS" apply -f - 2>&1 <<'YAML' | tee /tmp/kyverno-apply.log
apiVersion: v1
kind: Pod
metadata:
  name: e2e-unsigned-image-test
spec:
  containers:
    - name: c
      image: harbor.lolday.svc:80/lolday/nonexistent-unsigned:latest
  restartPolicy: Never
YAML
rc=$?
set -e
echo "::endgroup::"

if [ "$rc" -eq 0 ]; then
  echo "::error::Pod admission succeeded with an unsigned image; expected rejection"
  exit 1
fi

if ! grep -qE "$POLICY|signature" /tmp/kyverno-apply.log; then
  echo "::error::Pod was rejected but not by the $POLICY signature policy"
  cat /tmp/kyverno-apply.log
  exit 1
fi

echo "PASS: Kyverno rejected the unsigned Harbor image as expected"
