#!/usr/bin/env bash
# --allow-dirty: dirty subtree allowed, tag suffix `-dirty-<ts>`,
# helpers.lock NOT written.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"
export LOLDAY_REPO_ROOT_OVERRIDE="$repo"

# Dirty up the tree.
echo "// dirty" >> charts/lolday/helpers/build-helper/maldet_validator.py

# Without --allow-dirty, --dry-run still rejects.
if bash "$SCRIPT" --dry-run 2>/dev/null; then
  fail "default --dry-run accepted dirty subtree"
fi
pass "default mode refuses dirty subtree"

# With --allow-dirty + --dry-run, output contains the -dirty- suffix
# and the lock is not touched.
out="$(bash "$SCRIPT" --dry-run --allow-dirty 2>&1)"
echo "$out" | grep -Eq "build-helper:[0-9a-f]{12}-dirty-[0-9]+" \
  || fail "--allow-dirty did not stamp build-helper -dirty-<ts>"
echo "$out" | grep -Eq "job-helper:[0-9a-f]{12}-dirty-[0-9]+" \
  || fail "--allow-dirty did not stamp job-helper -dirty-<ts>"
pass "--allow-dirty stamps -dirty-<ts> tag for every helper"

[ ! -e "$repo/charts/lolday/helpers.lock" ] \
  || fail "--allow-dirty wrote helpers.lock — must not"
pass "--allow-dirty leaves helpers.lock untouched"
