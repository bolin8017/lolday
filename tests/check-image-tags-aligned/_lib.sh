#!/usr/bin/env bash
# Shared helpers for tests/check-image-tags-aligned/*.sh.
# Provides pass/fail logging plus a `mk_fixture` helper that materialises
# a tmpdir with a minimal `charts/lolday/{Chart.yaml,values.yaml}` pair.
# Tests then point the script-under-test at the fixture via
# LOLDAY_REPO_ROOT_OVERRIDE.
#
# Tests source this file and consume:
#   - REPO_ROOT    : absolute path of the lolday repo (the script under test)
#   - SCRIPT       : absolute path of scripts/check-image-tags-aligned.sh
#   - mk_fixture   : function. Usage:
#                      dir=$(mk_fixture)
#                      trap "rm -rf '$dir'" EXIT
#                      write_chart   "$dir" 1.2.3 1.2.3
#                      write_values  "$dir" <<'EOF'
#                        ...image lines...
#                      EOF
#                      LOLDAY_REPO_ROOT_OVERRIDE="$dir" bash "$SCRIPT"
#   - write_chart  : write_chart <fixture_dir> <version> <appVersion>
#   - write_values : write_values <fixture_dir>; reads values.yaml body from stdin
#
# The fixture is intentionally tiny — only the fields the script reads
# (Chart.yaml version/appVersion + values.yaml `image:` lines). Sub-chart
# blocks are added per-test when the test wants to assert scope-exclusion
# behaviour.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/check-image-tags-aligned.sh"

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Stable placeholder SHA-256 digests for use in fixture image refs.
# Real values; correctness is in the @sha256:<64-hex> shape, not the bytes.
# shellcheck disable=SC2034  # consumed by tests sourcing this file
DIGEST_A="sha256:$(printf 'a%.0s' {1..64})"
# shellcheck disable=SC2034
DIGEST_B="sha256:$(printf 'b%.0s' {1..64})"
# shellcheck disable=SC2034
DIGEST_C="sha256:$(printf 'c%.0s' {1..64})"

# Create an empty fixture root with charts/lolday/ inside.
mk_fixture() {
  local dir
  dir="$(mktemp -d)"
  mkdir -p "$dir/charts/lolday"
  echo "$dir"
}

# write_chart <dir> <version> <appVersion>
write_chart() {
  local dir=$1 version=$2 appversion=$3
  cat > "$dir/charts/lolday/Chart.yaml" <<EOF
apiVersion: v2
name: lolday
type: application
version: $version
appVersion: "$appversion"
EOF
}

# write_values <dir>; body from stdin.
# Indentation matches the real chart (2-space indent under top-level keys).
write_values() {
  local dir=$1
  cat > "$dir/charts/lolday/values.yaml"
}
