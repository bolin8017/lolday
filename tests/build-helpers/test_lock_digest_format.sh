#!/usr/bin/env bash
# scripts/check-helpers-lock.sh asserts every helpers.lock entry ends in
# @sha256:<64-hex> after the subtree-SHA tag (H-21-img). Verifies:
#   1. happy path with digest suffix → exit 0
#   2. lock without @sha256: suffix → exit 1 with the missing-digest msg
#   3. malformed (too-short) digest → exit 1 with the same msg
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

# Real-shape digests (64-hex chars) so the happy path matches the
# production format exactly. The values themselves are arbitrary —
# the script doesn't talk to Harbor for the check; it only validates
# the lock's textual shape.
build_digest="sha256:$(printf 'a%.0s' {1..64})"
job_digest="sha256:$(printf 'b%.0s' {1..64})"

mkdir -p charts/lolday

# --- happy path ---
cat > charts/lolday/helpers.lock <<EOF
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:$build_sha@$build_digest",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:$job_sha@$job_digest"
}
EOF
LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" \
  || fail "digest-pinned lock rejected"
pass "digest-pinned lock accepted"

# --- missing digest (tag-only, pre-H-21-img shape) ---
cat > charts/lolday/helpers.lock <<EOF
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:$build_sha",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:$job_sha"
}
EOF
out="$(LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>&1)" && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "tag-only lock accepted (should reject)"
echo "$out" | grep -qF "missing @sha256:<64-hex> digest pin" \
  || fail "tag-only lock rejection didn't surface the missing-digest msg: $out"
pass "tag-only lock rejected with missing-digest message"

# --- malformed digest (too short) ---
cat > charts/lolday/helpers.lock <<EOF
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:$build_sha@sha256:abc",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:$job_sha@sha256:abc"
}
EOF
out="$(LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>&1)" && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "malformed-digest lock accepted (should reject)"
echo "$out" | grep -qF "missing @sha256:<64-hex> digest pin" \
  || fail "malformed-digest lock rejection didn't surface the missing-digest msg: $out"
pass "malformed-digest lock rejected with missing-digest message"
