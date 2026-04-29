#!/usr/bin/env bash
# compute_sha NAME prints the first 12 hex chars of the subtree's tree
# SHA at HEAD. Stable across calls; differs when the subtree changes.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"
export LOLDAY_REPO_ROOT_OVERRIDE="$repo"

# Source the script under test. The script must expose compute_sha
# without running main(); guard it via $LOLDAY_BUILD_HELPERS_SOURCED.
LOLDAY_BUILD_HELPERS_SOURCED=1
# shellcheck disable=SC1090
. "$SCRIPT"

# Stability + format
got_a="$(compute_sha build-helper)"
got_b="$(compute_sha build-helper)"
[ "$got_a" = "$got_b" ] || fail "compute_sha not stable across calls ($got_a vs $got_b)"
[[ "$got_a" =~ ^[0-9a-f]{12}$ ]] || fail "compute_sha did not return 12 hex chars (got '$got_a')"
pass "compute_sha is stable + 12-hex format"

# SHA matches git rev-parse directly
expected="$(expected_sha "$repo" build-helper)"
[ "$got_a" = "$expected" ] || fail "compute_sha drift vs git rev-parse ($got_a vs $expected)"
pass "compute_sha matches git rev-parse"

# Different helper → different SHA
job_sha="$(compute_sha job-helper)"
[ "$job_sha" != "$got_a" ] || fail "build-helper and job-helper share SHA — fixture broken"
pass "different helpers produce different SHAs"

# Subtree mutation → SHA changes
echo "// extra" >> charts/lolday/helpers/build-helper/maldet_validator.py
git add -A
git commit -q -m "mutate build-helper"
new_sha="$(compute_sha build-helper)"
[ "$new_sha" != "$got_a" ] || fail "subtree mutation did not change SHA"
pass "subtree mutation shifts SHA"
