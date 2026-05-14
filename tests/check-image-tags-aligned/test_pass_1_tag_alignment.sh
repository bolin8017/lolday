#!/usr/bin/env bash
# scripts/check-image-tags-aligned.sh Pass 1:
# Chart.yaml version + appVersion + values.yaml lolday-backend /
# lolday-frontend tags must all be aligned (catches the half-bumped
# release pattern). The mlflow ref uses a NON-vX.Y.Z suffix
# (e.g. v2.20.3-boto3) — Pass 1 skips it; Pass 2 still requires a
# digest on it.
#
# Cases:
#   1. Chart.yaml at 1.2.3 + backend / frontend tags at v1.2.3 → exit 0.
#   2. Chart.yaml at 1.2.3 + backend tag still v1.2.2 → non-zero with
#      the `(from Chart.yaml version 1.2.3)` substring in the error.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

# ---- Case 1: aligned tags → exit 0 ----
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
  || fail "case 1 (aligned tags): hook rejected an aligned fixture: $out"
echo "$out" | grep -qF "image tags aligned with Chart.yaml: v1.2.3" \
  || fail "case 1: aligned-success line missing; got: $out"
pass "case 1: Chart.yaml 1.2.3 + backend/frontend v1.2.3 → exit 0"

# ---- Case 2: half-bumped backend tag → non-zero + parenthetical context ----
dir2=$(mk_fixture)
trap "rm -rf '$dir' '$dir2'" EXIT
write_chart "$dir2" 1.2.3 1.2.3
write_values "$dir2" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:v1.2.2@$DIGEST_A
mlflow:
  image: harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3@$DIGEST_B
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v1.2.3@$DIGEST_C
EOF
err=$(LOLDAY_REPO_ROOT_OVERRIDE="$dir2" bash "$SCRIPT" 2>&1) && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "case 2 (half-bumped backend tag): hook unexpectedly exited 0"
echo "$err" | grep -qF "(from Chart.yaml version 1.2.3)" \
  || fail "case 2: '(from Chart.yaml version 1.2.3)' annotation missing; got: $err"
echo "$err" | grep -qE 'ERROR: backend image tag v1\.2\.2 != expected v1\.2\.3' \
  || fail "case 2: backend-tag error preamble missing; got: $err"
pass "case 2: backend v1.2.2 vs Chart 1.2.3 → non-zero + parenthetical context"
