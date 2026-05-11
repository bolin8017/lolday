#!/usr/bin/env bash
# Live smoke test for the 2026-05-11 MLflow data-model redesign.
#
# Spec: docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md §8.3
#
# Pre-requisites (all must be true; the script does NOT check them):
#   - maldet 2.2.1 on PyPI
#   - elfrfdet 4.2.0 + elfcnndet 4.2.0 detector images built via lolday and
#     pushed to Harbor (DetectorVersion rows exist in DB)
#   - lolday backend deployed with the Plan B changes (alembic at head
#     1afdf61e18f9; create_run requires start_time_ms; reconciler finalizes
#     MLflow runs; provenance tags + experiment description present)
#   - The operator has kubectl access to the lolday K3s cluster
#
# Required env vars supplied by the operator:
#   LOLDAY_URL          — public base URL, e.g. https://lolday.connlabai.com
#   LOLDAY_COOKIE       — full Cookie header value with the Cf-Authorization
#                          + lolday session cookies (copy from browser devtools)
#   ELF_RF_42_DV_ID     — UUID of the elfrfdet 4.2.0 DetectorVersion row
#   TRAIN_DS_ID         — UUID of any existing train dataset
#   TEST_DS_ID          — UUID of any existing test dataset
#
# Tunables (have defaults):
#   NAMESPACE   default 'lolday'
#   MLFLOW_URL  default in-cluster service DNS
#   TIMEOUT_SECONDS  default 600

set -euo pipefail

NAMESPACE="${NAMESPACE:-lolday}"
MLFLOW_URL="${MLFLOW_URL:-http://mlflow.lolday.svc.cluster.local:5000}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"

: "${LOLDAY_URL:?LOLDAY_URL is required}"
: "${LOLDAY_COOKIE:?LOLDAY_COOKIE is required (browser devtools Cookie header)}"
: "${ELF_RF_42_DV_ID:?ELF_RF_42_DV_ID is required (elfrfdet 4.2.0 UUID)}"
: "${TRAIN_DS_ID:?TRAIN_DS_ID is required}"
: "${TEST_DS_ID:?TEST_DS_ID is required}"

fail() {
    printf '\xe2\x9c\x97 %s\n' "$*" >&2
    exit 1
}

ok() {
    printf '\xe2\x9c\x93 %s\n' "$*"
}

step() {
    printf '\n==> %s\n' "$*"
}

# kubectl-run an ephemeral curl pod for in-cluster MLflow access.
mlflow_curl() {
    local name="mlflow-smoke-$(date +%s)-$$"
    kubectl run -n "$NAMESPACE" "$name" --rm -i --restart=Never \
        --image=curlimages/curl:8.10.1 --quiet --command -- "$@"
}

step "Step 1: Submit a train job via lolday API"
JOB_RESP="$(curl -s -X POST "$LOLDAY_URL/api/v1/jobs" \
    -H "Cookie: $LOLDAY_COOKIE" \
    -H "Content-Type: application/json" \
    -d "$(cat <<EOF
{
    "detector_version_id": "$ELF_RF_42_DV_ID",
    "type": "train",
    "train_dataset_id": "$TRAIN_DS_ID",
    "test_dataset_id": "$TEST_DS_ID"
}
EOF
)")"
RUN_ID="$(echo "$JOB_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('mlflow_run_id',''))")"
JOB_ID="$(echo "$JOB_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('id',''))")"
[ -n "$RUN_ID" ] || fail "no mlflow_run_id in job response: $JOB_RESP"
[ -n "$JOB_ID" ] || fail "no job id in response: $JOB_RESP"
ok "job_id=$JOB_ID  mlflow_run_id=$RUN_ID"

step "Step 2: Wait for job to reach succeeded (timeout ${TIMEOUT_SECONDS}s)"
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT_SECONDS" ]; do
    STATUS="$(curl -s "$LOLDAY_URL/api/v1/jobs/$JOB_ID" \
        -H "Cookie: $LOLDAY_COOKIE" \
        | python -c "import sys,json; print(json.load(sys.stdin).get('status',''))")"
    case "$STATUS" in
        succeeded) ok "job reached succeeded"; break ;;
        failed|timeout|cancelled) fail "job ended with status=$STATUS" ;;
        "") fail "job status not retrievable (auth?)" ;;
        *) sleep 10; elapsed=$((elapsed + 10)) ;;
    esac
done
[ "$STATUS" = "succeeded" ] || fail "job did not succeed within ${TIMEOUT_SECONDS}s (last status=$STATUS)"

step "Step 3: Validate MLflow run info"
RUN_JSON="$(mlflow_curl curl -s "$MLFLOW_URL/api/2.0/mlflow/runs/get?run_id=$RUN_ID")"
START_TIME="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['start_time'])")"
END_TIME="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['end_time'])")"
MLFLOW_STATUS="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['status'])")"
[ "$START_TIME" -gt 0 ] 2>/dev/null || fail "info.start_time is 0 (spec § 4.2 regression)"
ok "info.start_time = $START_TIME (non-zero)"
[ "$END_TIME" -gt 0 ] 2>/dev/null || fail "info.end_time is 0"
ok "info.end_time   = $END_TIME"
[ "$MLFLOW_STATUS" = "FINISHED" ] || fail "info.status = $MLFLOW_STATUS (expected FINISHED)"
ok "info.status     = FINISHED"

step "Step 4: Validate provenance tags (spec § 5.7)"
TAGS_JSON="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.dumps({t['key']: t['value'] for t in json.load(sys.stdin)['run']['data']['tags']}))")"
for k in mlflow.source.name mlflow.source.type mlflow.source.git.commit \
         lolday.detector_image_digest lolday.maldet_version \
         lolday.resource_profile lolday.gpu_count \
         lolday.train_dataset_id lolday.test_dataset_id; do
    VAL="$(echo "$TAGS_JSON" | K="$k" python -c "import sys,json,os; print(json.load(sys.stdin).get(os.environ['K'], ''))")"
    [ -n "$VAL" ] || fail "tag $k is missing or empty"
    ok "tag $k = $VAL"
done

step "Step 5: Validate train artifacts (MLflow Models flavor)"
ART="$(mlflow_curl curl -s "$MLFLOW_URL/api/2.0/mlflow/artifacts/list?run_id=$RUN_ID")"
echo "$ART" | python -c "
import sys, json
files = [f['path'] for f in json.load(sys.stdin).get('files', [])]
assert 'model' in files, f'missing model/ dir: {files}'
" || fail "model/ artifact missing"
ok "model/ artifact present"

MODEL_LISTING="$(mlflow_curl curl -s "$MLFLOW_URL/api/2.0/mlflow/artifacts/list?run_id=$RUN_ID&path=model")"
echo "$MODEL_LISTING" | python -c "
import sys, json
files = [f['path'] for f in json.load(sys.stdin).get('files', [])]
assert any('MLmodel' in f for f in files), f'no MLmodel YAML under model/: {files}'
" || fail "MLmodel YAML missing under model/"
ok "MLmodel YAML present under model/"

step "Step 6: Validate system metrics auto-logging"
METRICS_JSON="$(echo "$RUN_JSON" | python -c "import sys,json; print(json.dumps({m['key']: m['value'] for m in json.load(sys.stdin)['run']['data']['metrics']}))")"
for k in system/cpu_utilization_percentage system/system_memory_usage_megabytes; do
    HAS="$(echo "$METRICS_JSON" | K="$k" python -c "import sys,json,os; d=json.load(sys.stdin); print('yes' if os.environ['K'] in d else 'no')")"
    [ "$HAS" = "yes" ] || fail "metric $k missing — psutil pin missing from detector image?"
    ok "metric $k present"
done

step "Step 7: Validate lolday Job timestamps surface in proxy enrichment"
RUN_VIA_PROXY="$(curl -s "$LOLDAY_URL/api/v1/runs/$RUN_ID" -H "Cookie: $LOLDAY_COOKIE")"
LOLDAY_STARTED="$(echo "$RUN_VIA_PROXY" | python -c "import sys,json; print(json.load(sys.stdin).get('lolday_started_at',''))")"
LOLDAY_FINISHED="$(echo "$RUN_VIA_PROXY" | python -c "import sys,json; print(json.load(sys.stdin).get('lolday_finished_at',''))")"
[ -n "$LOLDAY_STARTED" ] && [ "$LOLDAY_STARTED" != "None" ] || fail "lolday_started_at missing from /api/v1/runs/{id} response"
[ -n "$LOLDAY_FINISHED" ] && [ "$LOLDAY_FINISHED" != "None" ] || fail "lolday_finished_at missing"
ok "lolday_started_at = $LOLDAY_STARTED"
ok "lolday_finished_at = $LOLDAY_FINISHED"

step "Step 8: Negative test — kill a vcjob mid-run, verify reconciler finalizes MLflow"
NEG_RESP="$(curl -s -X POST "$LOLDAY_URL/api/v1/jobs" \
    -H "Cookie: $LOLDAY_COOKIE" \
    -H "Content-Type: application/json" \
    -d "$(cat <<EOF
{
    "detector_version_id": "$ELF_RF_42_DV_ID",
    "type": "train",
    "train_dataset_id": "$TRAIN_DS_ID",
    "test_dataset_id": "$TEST_DS_ID"
}
EOF
)")"
NEG_RUN_ID="$(echo "$NEG_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('mlflow_run_id',''))")"
NEG_JOB_ID="$(echo "$NEG_RESP" | python -c "import sys,json; print(json.load(sys.stdin).get('id',''))")"
[ -n "$NEG_RUN_ID" ] && [ -n "$NEG_JOB_ID" ] || fail "negative job submission failed: $NEG_RESP"

# Wait for the vcjob to enter PREPARING (the K8s vcjob exists).
sleep 30
JOB_SHORT="${NEG_JOB_ID//-/}"
# Volcano names use 'train-' prefix from JobType + the dashless UUID.
VCJOB="$(kubectl get vcjob -n "$NAMESPACE" -o name 2>/dev/null \
    | grep -i "$(echo "$JOB_SHORT" | head -c 8)" | head -1)"
if [ -z "$VCJOB" ]; then
    fail "could not locate vcjob for $NEG_JOB_ID — was it queued indefinitely?"
fi
kubectl delete -n "$NAMESPACE" "$VCJOB" --wait=false || true

# Wait for the reconciler (period ~10s) to notice + call _finalize_mlflow_run.
sleep 40

NEG_RUN_JSON="$(mlflow_curl curl -s "$MLFLOW_URL/api/2.0/mlflow/runs/get?run_id=$NEG_RUN_ID")"
NEG_STATUS="$(echo "$NEG_RUN_JSON" | python -c "import sys,json; print(json.load(sys.stdin)['run']['info']['status'])")"
[ "$NEG_STATUS" = "FAILED" ] || fail "killed-job MLflow run status = $NEG_STATUS (expected FAILED) — _finalize_mlflow_run did not fire"
ok "killed-job MLflow run status = FAILED — reconciler finalize chain works"

printf '\n\xe2\x9c\x85 All 8 checks passed.\n'
