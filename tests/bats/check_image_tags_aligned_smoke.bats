#!/usr/bin/env bats
# Smoke for scripts/check-image-tags-aligned.sh — the pre-commit guard that
# pins Chart.yaml ↔ values.yaml image tags together AND enforces H-21-img's
# @sha256:<64-hex> digest pin on every `image:` scalar in values.yaml.
#
# Exit codes:
#   0 — both assertions pass (versions aligned + digest pin present on every image:)
#   1 — divergence detected (missing field, mismatched version, missing @sha256 pin)
#
# Test strategy: synthesize a throwaway Chart.yaml + values.yaml under
# LOLDAY_REPO_ROOT_OVERRIDE so each case starts from a known-good baseline
# and mutates exactly one field. Mirrors check_helpers_lock_smoke.bats.

setup() {
  REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
  SCRIPT="${REPO_ROOT}/scripts/check-image-tags-aligned.sh"
  # 64-hex SHA placeholder used across the synthetic fixtures.
  DIGEST="@sha256:$(printf '%064d' 0)"
}

_seed_repo() {
  # Args: $1=chart_version $2=chart_appversion $3=backend_tag $4=frontend_tag
  #       $5=backend_digest_suffix $6=frontend_digest_suffix
  local cv="$1" av="$2" bt="$3" ft="$4" bd="$5" fd="$6"
  TMP="$(mktemp -d)"
  mkdir -p "${TMP}/charts/lolday"
  cat > "${TMP}/charts/lolday/Chart.yaml" <<EOF
apiVersion: v2
name: lolday
version: ${cv}
appVersion: "${av}"
EOF
  cat > "${TMP}/charts/lolday/values.yaml" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:${bt}${bd}
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:${ft}${fd}
EOF
  echo "$TMP"
}

@test "exits 0 when chart + values + digest pin all align" {
  TMP="$(_seed_repo "1.2.3" "1.2.3" "v1.2.3" "v1.2.3" "$DIGEST" "$DIGEST")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "image tags aligned"
}

@test "exits 1 when appVersion diverges from version" {
  TMP="$(_seed_repo "1.2.3" "1.2.4" "v1.2.3" "v1.2.3" "$DIGEST" "$DIGEST")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "appVersion"
}

@test "exits 1 when backend image tag diverges from Chart.yaml version" {
  TMP="$(_seed_repo "1.2.3" "1.2.3" "v1.2.2" "v1.2.3" "$DIGEST" "$DIGEST")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "backend image tag"
}

@test "exits 1 when frontend image tag diverges from Chart.yaml version" {
  TMP="$(_seed_repo "1.2.3" "1.2.3" "v1.2.3" "v1.2.2" "$DIGEST" "$DIGEST")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "frontend image tag"
}

@test "exits 1 when backend image is missing the @sha256 pin" {
  # Versions align, but the backend reference lacks the @sha256:<64-hex> suffix.
  TMP="$(_seed_repo "1.2.3" "1.2.3" "v1.2.3" "v1.2.3" "" "$DIGEST")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "missing @sha256"
}

@test "exits 1 when frontend image is missing the @sha256 pin" {
  TMP="$(_seed_repo "1.2.3" "1.2.3" "v1.2.3" "v1.2.3" "$DIGEST" "")"
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "missing @sha256"
}

@test "exits 1 with parse error when Chart.yaml is missing the version: line" {
  # The script's first assertion checks all four parsed fields are non-empty;
  # the "could not parse" branch fires when awk returns an empty string (e.g.
  # the `version:` line is absent), distinct from the `grep | head | sed`
  # pipefail path that aborts the script earlier.
  TMP="$(mktemp -d)"
  mkdir -p "${TMP}/charts/lolday"
  cat > "${TMP}/charts/lolday/Chart.yaml" <<EOF
apiVersion: v2
name: lolday
appVersion: "1.2.3"
EOF
  cat > "${TMP}/charts/lolday/values.yaml" <<EOF
backend:
  image: harbor.lolday.svc:80/lolday/lolday-backend:v1.2.3${DIGEST}
frontend:
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v1.2.3${DIGEST}
EOF
  run env LOLDAY_REPO_ROOT_OVERRIDE="$TMP" bash "${SCRIPT}"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "could not parse"
}
