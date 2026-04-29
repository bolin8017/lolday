#!/usr/bin/env bash
# --only NAME limits the run to a single helper.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"
export LOLDAY_REPO_ROOT_OVERRIDE="$repo"

build_sha="$(expected_sha "$repo" build-helper)"
job_sha="$(expected_sha "$repo" job-helper)"

out="$(bash "$SCRIPT" --dry-run --only build-helper 2>&1)"
echo "$out" | grep -qF "build-helper:$build_sha" \
  || fail "--only build-helper missed build-helper output"
echo "$out" | grep -qF "job-helper" \
  && fail "--only build-helper still mentioned job-helper"
pass "--only build-helper isolates to build-helper"

# Unknown helper → exit non-zero
if bash "$SCRIPT" --dry-run --only nope 2>/dev/null; then
  fail "--only nope did not exit non-zero"
fi
pass "--only nope rejected"
