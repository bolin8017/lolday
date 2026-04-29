#!/usr/bin/env bash
# --dry-run prints the SHAs for both helpers without touching docker /
# kubectl / Harbor. The output line shape is fixed; downstream tooling
# (CI, README copy/paste) depends on it.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"
export LOLDAY_REPO_ROOT_OVERRIDE="$repo"

# Run with --dry-run. We intentionally do NOT define a kubectl mock —
# the script must short-circuit before any kubectl call.
out="$(bash "$SCRIPT" --dry-run 2>&1)"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

echo "$out" | grep -qF "build-helper:$build_sha" \
  || fail "dry-run did not print expected build-helper SHA"
pass "dry-run prints build-helper:$build_sha"

echo "$out" | grep -qF "job-helper:$job_sha" \
  || fail "dry-run did not print expected job-helper SHA"
pass "dry-run prints job-helper:$job_sha"

# Lock must NOT be touched in --dry-run.
[ ! -e "$repo/charts/lolday/helpers.lock" ] \
  || fail "dry-run wrote helpers.lock — it must not"
pass "dry-run leaves helpers.lock untouched"
