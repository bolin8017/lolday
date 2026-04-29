#!/usr/bin/env bash
# check_clean NAME exits 0 on a clean subtree and 1 on either an
# uncommitted modification or an untracked file inside it. Shallow-clone
# detection lives in assert_not_shallow.
set -euo pipefail
. "$(dirname "$0")/_lib.sh"

repo="$(mk_fixture)"
trap "rm -rf '$repo'" EXIT
cd "$repo"

# Override REPO_ROOT via LOLDAY_REPO_ROOT_OVERRIDE so the sourced
# script targets the fixture repo, not the real lolday repo.
export LOLDAY_REPO_ROOT_OVERRIDE="$repo"
LOLDAY_BUILD_HELPERS_SOURCED=1
# shellcheck disable=SC1090
. "$SCRIPT"

# Clean tree → exit 0
if check_clean build-helper; then
  pass "clean subtree accepted"
else
  fail "clean subtree rejected"
fi

# Modify a tracked file → exit 1
echo "// dirty" >> charts/lolday/helpers/build-helper/maldet_validator.py
if check_clean build-helper 2>/dev/null; then
  fail "modified subtree accepted (should be rejected)"
fi
pass "modified subtree rejected"

# Roll back, add an untracked file → exit 1
git checkout -- charts/lolday/helpers/build-helper/maldet_validator.py
touch charts/lolday/helpers/build-helper/UNTRACKED.tmp
if check_clean build-helper 2>/dev/null; then
  fail "subtree with untracked file accepted (should be rejected)"
fi
pass "untracked file in subtree rejected"
rm charts/lolday/helpers/build-helper/UNTRACKED.tmp

# Shallow-clone detection (independent of subtree state).
shallow="$(mktemp -d)"
( cd "$shallow" && git clone --depth=1 -q "file://$repo" clone )
(
  cd "$shallow/clone"
  export LOLDAY_REPO_ROOT_OVERRIDE="$shallow/clone"
  LOLDAY_BUILD_HELPERS_SOURCED=1
  # shellcheck disable=SC1090
  . "$SCRIPT"
  if assert_not_shallow 2>/dev/null; then
    exit 1   # signals fail
  else
    exit 0   # expected — shallow refused
  fi
) || fail "assert_not_shallow accepted a shallow clone"
pass "shallow clone rejected"
rm -rf "$shallow"
