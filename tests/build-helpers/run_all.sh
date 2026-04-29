#!/usr/bin/env bash
# Run every test_*.sh in this directory, report pass/fail, exit non-zero
# if any test file fails (after running every file — does not short-
# circuit on the first failure). Run with `bash tests/build-helpers/run_all.sh`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shopt -s nullglob

failed=0
for t in "$SCRIPT_DIR"/test_*.sh; do
  printf '\n--- %s ---\n' "$(basename "$t")"
  if bash "$t"; then
    :
  else
    failed=$((failed + 1))
  fi
done

if [ "$failed" -gt 0 ]; then
  printf '\n\033[31m%d test file(s) failed\033[0m\n' "$failed" >&2
  exit 1
fi
printf '\n\033[32mAll build-helpers tests passed\033[0m\n'
