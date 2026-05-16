#!/usr/bin/env bats
# D4.1 — smoke for scripts/build-helpers.sh
#
# Covers the non-network paths: --help (no side effects) and the argv
# error branches (unknown flag, --only with no NAME, --only with NAME
# not in HELPERS=()). The --dry-run path needs LOLDAY_REPO_ROOT_OVERRIDE
# pointed at a git tree that has charts/lolday/helpers/{build,job}-helper
# subtrees, so we set one up via setup().

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/build-helpers.sh"
  TMP="$(mktemp -d)"
  pushd "$TMP" >/dev/null
  git init -q
  git config user.email t@t && git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo from-fixture > charts/lolday/helpers/build-helper/Dockerfile
  echo from-fixture > charts/lolday/helpers/job-helper/Dockerfile
  git add -A && git commit -qm seed
  popd >/dev/null
  export LOLDAY_REPO_ROOT_OVERRIDE="$TMP"
}

teardown() {
  rm -rf "$TMP"
}

@test "--help prints usage and exits 0" {
  run bash "${SCRIPT}" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage:"
  echo "$output" | grep -q -- "--dry-run"
}

@test "unknown flag exits 1 with error" {
  run bash "${SCRIPT}" --bogus-flag
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "unknown flag"
}

@test "--only without a NAME exits 1" {
  run bash "${SCRIPT}" --only
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "requires a NAME"
}

@test "--only NAME not in HELPERS exits 1 from main shell" {
  run bash "${SCRIPT}" --only does-not-exist --dry-run
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "not in HELPERS"
}

@test "--dry-run prints the helper refs without calling docker" {
  run bash "${SCRIPT}" --dry-run
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "\[dry-run\] build-helper"
  echo "$output" | grep -q "\[dry-run\] job-helper"
}
