#!/usr/bin/env bash
# Phase 6 smoke — sla plugin + no-GPU-quota together prevent the
# Test-D leapfrog (d-BIG = gpu=2, d-SMALL = gpu=1, jobs free at
# staggered times). Spec: docs/superpowers/specs/2026-05-05-gpu-fifo-
# anti-starvation-design.md §4.4 + §6.

set -euo pipefail

NS_INFRA=${NS_INFRA:-lolday}
NS_JOBS=${NS_JOBS:-lolday-jobs}
SCHED_CM=${SCHED_CM:-lolday-scheduler-configmap}
SCHED_DEPLOY=${SCHED_DEPLOY:-lolday-scheduler}
TEST_SLA_WAIT=${TEST_SLA_WAIT:-20s}
IMAGE=${IMAGE:-nvidia/cuda:12.6.3-base-ubuntu22.04}

ORIG_CONF=""
cleanup() {
  echo
  echo "[cleanup] deleting test jobs"
  kubectl -n "$NS_JOBS" delete jobs.batch.volcano.sh \
    -l lolday.test=phase6-fifo --wait=true 2>/dev/null || true
  if [ -n "$ORIG_CONF" ]; then
    echo "[cleanup] restoring scheduler config to original sla-waiting-time"
    kubectl -n "$NS_INFRA" patch cm "$SCHED_CM" --type=merge \
      -p "{\"data\":{\"volcano-scheduler.conf\":$(printf '%s' "$ORIG_CONF" | jq -R -s .)}}" \
      >/dev/null
    kubectl -n "$NS_INFRA" rollout restart deploy "$SCHED_DEPLOY" >/dev/null 2>&1 || true
    kubectl -n "$NS_INFRA" rollout status deploy "$SCHED_DEPLOY" --timeout=2m >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

submit_job() {
  local name=$1 gpu=$2 sleep_s=$3
  cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: batch.volcano.sh/v1alpha1
kind: Job
metadata:
  name: $name
  namespace: $NS_JOBS
  labels:
    lolday.test: phase6-fifo
    lolday.test-name: $name
spec:
  schedulerName: volcano
  minAvailable: 1
  queue: lolday-training
  ttlSecondsAfterFinished: 60
  tasks:
  - name: main
    replicas: 1
    policies:
    - event: TaskCompleted
      action: CompleteJob
    template:
      metadata:
        labels:
          lolday.test: phase6-fifo
          lolday.test-name: $name
      spec:
        restartPolicy: Never
        nodeSelector:
          kubernetes.io/hostname: server30
        securityContext:
          runAsNonRoot: true
          runAsUser: 1000
          fsGroup: 1000
        containers:
        - name: gpu-busy
          image: $IMAGE
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh","-c"]
          args: ["sleep $sleep_s"]
          resources:
            requests: { cpu: 200m, memory: 256Mi }
            limits: { cpu: 1, memory: 1Gi, nvidia.com/gpu: $gpu }
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
EOF
}

echo "[step 1/5] scheduler config has sla plugin in tier 1"
plugins=$(kubectl -n "$NS_INFRA" get cm "$SCHED_CM" -o jsonpath='{.data.volcano-scheduler\.conf}')
if echo "$plugins" | grep -q "name: sla"; then
  echo "OK"
else
  echo "FAIL: sla plugin not in scheduler config"
  exit 1
fi

echo
echo "[step 2/5] lolday-jobs-quota has no nvidia.com/gpu axis"
if kubectl -n "$NS_JOBS" get resourcequota lolday-jobs-quota \
    -o jsonpath='{.spec.hard}' 2>/dev/null | grep -q "nvidia.com/gpu"; then
  echo "FAIL: lolday-jobs-quota still has nvidia.com/gpu"
  exit 1
fi
echo "OK"

echo
echo "[step 3/5] lower sla-waiting-time to $TEST_SLA_WAIT for fast test"
ORIG_CONF=$(kubectl -n "$NS_INFRA" get cm "$SCHED_CM" \
  -o jsonpath='{.data.volcano-scheduler\.conf}')
NEW_CONF=$(echo "$ORIG_CONF" \
  | sed -E "s/sla-waiting-time: [0-9a-z]+/sla-waiting-time: $TEST_SLA_WAIT/")
kubectl -n "$NS_INFRA" patch cm "$SCHED_CM" --type=merge \
  -p "{\"data\":{\"volcano-scheduler.conf\":$(printf '%s' "$NEW_CONF" | jq -R -s .)}}" \
  >/dev/null
kubectl -n "$NS_INFRA" rollout restart deploy "$SCHED_DEPLOY" >/dev/null
kubectl -n "$NS_INFRA" rollout status deploy "$SCHED_DEPLOY" --timeout=2m
# Defense against silent sed no-op: confirm the patched value is now present.
kubectl -n "$NS_INFRA" get cm "$SCHED_CM" \
  -o jsonpath='{.data.volcano-scheduler\.conf}' \
  | grep -q "sla-waiting-time: $TEST_SLA_WAIT" \
  || { echo "FAIL: sed did not replace sla-waiting-time in ConfigMap"; exit 1; }
sleep 5
echo "OK"

echo
echo "[step 4/5] Test D scenario — staggered finish, head-of-line gpu=2"
submit_job d-j1 1 30
submit_job d-j2 1 70
sleep 5
submit_job d-big 2 15
sleep 4
submit_job d-small 1 15
# Diagnostic — actual job creationTimestamp gap (helps debug if smoke fails on slow clusters)
big_ct=$(kubectl -n "$NS_JOBS" get jobs.batch.volcano.sh d-big -o jsonpath='{.metadata.creationTimestamp}')
small_ct=$(kubectl -n "$NS_JOBS" get jobs.batch.volcano.sh d-small -o jsonpath='{.metadata.creationTimestamp}')
echo "  d-big  creationTimestamp=$big_ct"
echo "  d-small creationTimestamp=$small_ct"
echo "submitted d-j1, d-j2, d-big, d-small"

echo
echo "[step 5/5] waiting up to 120s for d-BIG and d-SMALL pod startTime"
big_start=""
small_start=""
deadline=$(($(date +%s) + 120))
while (( $(date +%s) < deadline )); do
  [ -z "$big_start" ] && big_start=$(kubectl -n "$NS_JOBS" get pod d-big-main-0 \
    -o jsonpath='{.status.startTime}' 2>/dev/null || true)
  [ -z "$small_start" ] && small_start=$(kubectl -n "$NS_JOBS" get pod d-small-main-0 \
    -o jsonpath='{.status.startTime}' 2>/dev/null || true)
  if [ -n "$big_start" ] && [ -n "$small_start" ]; then break; fi
  sleep 4
done

if [ -z "$big_start" ] || [ -z "$small_start" ]; then
  echo "FAIL: missing startTime — big='$big_start' small='$small_start'"
  exit 1
fi

if [[ "$big_start" < "$small_start" ]]; then
  echo "OK: d-BIG ($big_start) scheduled before d-SMALL ($small_start) — sla worked"
else
  echo "FAIL: d-BIG ($big_start) scheduled AFTER d-SMALL ($small_start) — leapfrog still happens"
  exit 1
fi

echo
echo "=== PHASE 6 SMOKE PASSED ==="
