#!/usr/bin/env bats
# D4.1 — smoke for scripts/check-helpers-lock.sh
#
# Exit codes:
#   0 — bypass via LOLDAY_SKIP_HELPERS_LOCK_CHECK=1; or lock matches HEAD
#   1 — lock missing, or lock drifts from HEAD, or missing @sha256 pin

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/check-helpers-lock.sh"
}

@test "exits 0 when LOLDAY_SKIP_HELPERS_LOCK_CHECK=1" {
  run env LOLDAY_SKIP_HELPERS_LOCK_CHECK=1 bash "${SCRIPT}"
  [ "$status" -eq 0 ]
}

@test "exits 1 when lock file is missing" {
  TMP="$(mktemp -d)"
  cd "$TMP"
  git init -q
  git config user.email t@t
  git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo dummy > charts/lolday/helpers/build-helper/x
  echo dummy > charts/lolday/helpers/job-helper/x
  git add -A
  git commit -qm seed
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "helpers.lock missing"
}

@test "exits 1 when lock SHA disagrees with HEAD subtree" {
  TMP="$(mktemp -d)"
  cd "$TMP"
  git init -q
  git config user.email t@t
  git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo a > charts/lolday/helpers/build-helper/x
  echo a > charts/lolday/helpers/job-helper/x
  git add -A && git commit -qm seed
  cat > charts/lolday/helpers.lock <<JSON
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:000000000000@sha256:$(printf '%064d' 0)",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:000000000000@sha256:$(printf '%064d' 0)"
}
JSON
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "drift detected"
}

@test "exits 1 when lock entry missing @sha256 pin" {
  TMP="$(mktemp -d)"
  cd "$TMP"
  git init -q
  git config user.email t@t && git config user.name t
  mkdir -p charts/lolday/helpers/build-helper charts/lolday/helpers/job-helper
  echo a > charts/lolday/helpers/build-helper/x
  echo a > charts/lolday/helpers/job-helper/x
  git add -A && git commit -qm seed
  BSHA=$(git rev-parse --short=12 HEAD:charts/lolday/helpers/build-helper)
  JSHA=$(git rev-parse --short=12 HEAD:charts/lolday/helpers/job-helper)
  cat > charts/lolday/helpers.lock <<JSON
{
  "build_helper": "harbor.lolday.svc:80/lolday/build-helper:${BSHA}",
  "job_helper":   "harbor.lolday.svc:80/lolday/job-helper:${JSHA}"
}
JSON
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "missing @sha256"
}
