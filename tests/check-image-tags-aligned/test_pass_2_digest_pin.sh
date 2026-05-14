#!/usr/bin/env bash
# scripts/check-image-tags-aligned.sh Pass 2:
# every top-level `image:` scalar in values.yaml must end in
# @sha256:<64-hex>. T4 widened this from "lolday-owned only" to
# "every image: scalar", so sub-chart full-ref scalars (postgres,
# redis, cloudflared, postgres-exporter) are now ENFORCED. Nested
# image.tag slots under Harbor / loki sidecar look like generic
# `tag:` keys and are not statically greppable — those are validated
# at deploy time via `helm template | grep` (plan T4 Step 5).
#
# Cases:
#   1. All three lolday-owned refs pinned → exit 0 with expected stdout.
#   2. One ref's digest stripped → exit non-zero with the missing-digest
#      error on stderr.
#   3. Sub-chart refs enforced (T4 scope): an unpinned
#      `image: postgres:16-alpine` line now fails the hook with the
#      same missing-digest error.
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

# ---- Case 3: sub-chart refs enforced (T4 scope) → non-zero ----
# Post-T4 the Pass 2 grep covers every top-level `image:` scalar, not
# just lolday-owned refs. An unpinned `image: postgres:16-alpine` line
# must now trip the same missing-digest error.
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
  image: redis:7.4-alpine@$DIGEST_A
EOF
err=$(LOLDAY_REPO_ROOT_OVERRIDE="$dir3" bash "$SCRIPT" 2>&1) && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "case 3 (sub-chart refs enforced): hook unexpectedly exited 0 on an unpinned sub-chart ref"
echo "$err" | grep -qE 'ERROR: image ref missing @sha256:<64-hex> digest pin: postgres:16-alpine$' \
  || fail "case 3: expected missing-digest error for postgres:16-alpine not seen; got: $err"
pass "case 3: sub-chart refs enforced (T4 scope) → non-zero + missing-digest error"
