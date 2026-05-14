#!/usr/bin/env bash
# scripts/check-helpers-lock.sh exits 0 when the lock matches HEAD,
# exits 1 with a drift message otherwise, and exits 0 unconditionally
# when LOLDAY_SKIP_HELPERS_LOCK_CHECK=1.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"
# H-21-img: lock entries carry an @sha256:<64-hex> suffix. Use real-shape
# placeholder digests so this test stays green alongside the new
# test_lock_digest_format.sh assertion. The check script doesn't talk to
# Harbor — it only validates textual shape — so the digest values can be
# arbitrary as long as they match the regex.
build_digest="sha256:$(printf 'a%.0s' {1..64})"
job_digest="sha256:$(printf 'b%.0s' {1..64})"

# Seed an in-sync lock.
mkdir -p charts/lolday
cat > charts/lolday/helpers.lock <<EOF
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:$build_sha@$build_digest",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:$job_sha@$job_digest"
}
EOF

# Override REPO_ROOT inside the script via env (the script picks it up).
LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" \
  || fail "in-sync lock rejected"
pass "in-sync lock accepted"

# Now drift it: rewrite build_helper SHA to bogus value.
sed -i 's/'"$build_sha"'/0000000deadb/' charts/lolday/helpers.lock
if LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>/dev/null; then
  fail "drifted lock accepted"
fi
pass "drifted lock rejected"

# Skip env honoured.
LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 \
  LOLDAY_REPO_ROOT_OVERRIDE="$repo" \
  bash "$CHECK_SCRIPT" \
  || fail "LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 still rejected"
pass "LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 short-circuits"

# Missing lock → exit 1 with a friendly message.
rm charts/lolday/helpers.lock
out="$(LOLDAY_REPO_ROOT_OVERRIDE="$repo" bash "$CHECK_SCRIPT" 2>&1)" && rc=$? || rc=$?
[ "${rc:-0}" -ne 0 ] || fail "missing lock did not exit non-zero"
echo "$out" | grep -qF "helpers.lock missing" \
  || fail "missing-lock message not surfaced"
pass "missing lock rejected with friendly message"
