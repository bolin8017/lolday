#!/usr/bin/env bash
# scripts/check-image-tags-aligned.sh Pass 2:
# every lolday-owned (harbor.lolday.svc:80/lolday/...) image ref in
# values.yaml must end in @sha256:<64-hex>. Sub-chart refs
# (postgres, redis, …) are out of scope until T4.
#
# Cases:
#   1. All three lolday-owned refs pinned → exit 0 with expected stdout.
#   2. One ref's digest stripped → exit non-zero with the missing-digest
#      error on stderr.
#   3. Adds a sub-chart-style `image: postgres:16-alpine` line (no
#      digest) → still exits 0. Scope-creep guard: a future PR that
#      widens Pass 2's grep to non-lolday refs will trip this test.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

# ---- Case 1: happy path — all three refs digest-pinned ----
dir=$(mk_fixture)
trap "rm -rf '$dir'" EXIT
write_chart "$dir" 1.2.3 1.2.3
write_values "$dir" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:v1.2.3@$DIGEST_A
mlflow:
  image: harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3@$DIGEST_B
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v1.2.3@$DIGEST_C
EOF
out=$(LOLDAY_REPO_ROOT_OVERRIDE="$dir" bash "$SCRIPT" 2>&1) \
  || fail "case 1 (all pinned): hook rejected a fully digest-pinned fixture: $out"
echo "$out" | grep -qF "image tags aligned with Chart.yaml: v1.2.3; digest pin present on all values.yaml image refs" \
  || fail "case 1: expected stdout line not seen; got: $out"
pass "case 1: all three lolday refs digest-pinned → exit 0 with success line"

# ---- Case 2: one digest stripped → non-zero + missing-digest error ----
dir2=$(mk_fixture)
trap "rm -rf '$dir' '$dir2'" EXIT
write_chart "$dir2" 1.2.3 1.2.3
write_values "$dir2" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:v1.2.3
mlflow:
  image: harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3@$DIGEST_B
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v1.2.3@$DIGEST_C
EOF
err=$(LOLDAY_REPO_ROOT_OVERRIDE="$dir2" bash "$SCRIPT" 2>&1) && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "case 2 (digest stripped): hook unexpectedly exited 0"
echo "$err" | grep -qE 'ERROR: image ref missing @sha256:<64-hex> digest pin: harbor\.lolday\.svc:80/lolday/lolday-backend:v1\.2\.3$' \
  || fail "case 2: missing-digest error message not surfaced; got: $err"
pass "case 2: one digest stripped → non-zero + missing-digest error"

# ---- Case 3: sub-chart-style `postgres:16-alpine` ref → still exit 0 ----
# Scope guard. T1 only checks harbor.lolday.svc:80/lolday/... refs. If a
# future PR rewrites the grep to cover *every* `image:` line, this case
# will start failing and CI will catch it before merge.
dir3=$(mk_fixture)
trap "rm -rf '$dir' '$dir2' '$dir3'" EXIT
write_chart "$dir3" 1.2.3 1.2.3
write_values "$dir3" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:v1.2.3@$DIGEST_A
mlflow:
  image: harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3@$DIGEST_B
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v1.2.3@$DIGEST_C
postgresql:
  image: postgres:16-alpine
redis:
  image: redis:7.4-alpine
EOF
out=$(LOLDAY_REPO_ROOT_OVERRIDE="$dir3" bash "$SCRIPT" 2>&1) \
  || fail "case 3 (sub-chart scope-exclusion): hook rejected a fixture with unpinned sub-chart refs (T1 must not check non-lolday refs): $out"
echo "$out" | grep -qF "image tags aligned with Chart.yaml: v1.2.3; digest pin present on all values.yaml image refs" \
  || fail "case 3: success stdout missing; got: $out"
pass "case 3: sub-chart refs ignored by Pass 2 (T1 scope) → exit 0"
