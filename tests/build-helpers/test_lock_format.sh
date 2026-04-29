#!/usr/bin/env bash
# write_lock REF_BUILD_HELPER REF_JOB_HELPER writes JSON with snake_case
# keys, ASCII output, two-space indent, and trailing newline. Atomic:
# the target file is replaced via rename, never partial.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

export LOLDAY_REPO_ROOT_OVERRIDE="$repo"
LOLDAY_BUILD_HELPERS_SOURCED=1
LOCK_FILE="$repo/charts/lolday/helpers.lock"
# shellcheck disable=SC1090
. "$SCRIPT"

write_lock \
  "harbor.lolday.svc:80/lolday/build-helper:abc123def456" \
  "harbor.lolday.svc:80/lolday/job-helper:0123456789ab"

[ -f "$LOCK_FILE" ] || fail "lock file not created"

# Last byte must be a newline.
last_byte="$(tail -c1 "$LOCK_FILE" | od -An -tx1 | tr -d ' ')"
[ "$last_byte" = "0a" ] || fail "lock file missing trailing newline"
pass "trailing newline present"

# Parses as JSON, has exactly the two expected keys, and the values
# match what we wrote.
python3 - "$LOCK_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
expected = {
    "build_helper": "harbor.lolday.svc:80/lolday/build-helper:abc123def456",
    "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:0123456789ab",
}
assert d == expected, f"unexpected lock contents: {d}"
PY
pass "JSON parses + contains expected keys/values"
