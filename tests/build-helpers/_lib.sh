#!/usr/bin/env bash
# Shared helpers for tests/build-helpers/*.sh.
# Provides pass/fail logging plus a `make_fixture_repo` helper that
# materialises an isolated git repo with two helper subtrees.
#
# Tests source this file and consume:
#   - REPO_ROOT    : absolute path of the lolday repo (the script under test)
#   - SCRIPT       : absolute path of scripts/build-helpers.sh
#   - CHECK_SCRIPT : absolute path of scripts/check-helpers-lock.sh
#   - mk_fixture   : function returning the path to a freshly-built fixture
#                    repo. Caller MUST cd into it AND register cleanup,
#                    AND export LOLDAY_REPO_ROOT_OVERRIDE before sourcing
#                    scripts/build-helpers.sh:
#                      dir=$(mk_fixture)
#                      trap 'rm -rf "$dir"' EXIT
#                      cd "$dir"
#                      export LOLDAY_REPO_ROOT_OVERRIDE="$dir"
#   - expected_sha : helper that prints the 12-char subtree SHA at HEAD.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/build-helpers.sh"
CHECK_SCRIPT="$REPO_ROOT/scripts/check-helpers-lock.sh"

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Build a throwaway git repo that mirrors the relevant slice of the
# lolday tree (charts/lolday/helpers/{build-helper,job-helper}). The
# subtree contents are intentionally tiny — tests care about SHA
# stability, not Dockerfile correctness.
mk_fixture() {
  local dir
  dir="$(mktemp -d)"
  (
    cd "$dir"
    git init -q -b main
    git config user.email "test@lolday.dev"
    git config user.name "Lolday Test"
    mkdir -p charts/lolday/helpers/build-helper
    mkdir -p charts/lolday/helpers/job-helper
    printf 'FROM python:3.12-slim\n' > charts/lolday/helpers/build-helper/Dockerfile
    printf 'placeholder\n'           > charts/lolday/helpers/build-helper/maldet_validator.py
    printf 'FROM python:3.12-slim\n' > charts/lolday/helpers/job-helper/Dockerfile
    printf 'placeholder\n'           > charts/lolday/helpers/job-helper/main.py
    git add -A
    git commit -q -m "initial fixture"
  )
  echo "$dir"
}

# Print the expected 12-char subtree SHA from the fixture repo.
expected_sha() {
  local repo=$1 helper=$2
  ( cd "$repo" && git rev-parse --short=12 "HEAD:charts/lolday/helpers/$helper" )
}
