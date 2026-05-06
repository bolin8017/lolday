#!/usr/bin/env bash
# Asserts that lolday-backend / lolday-frontend image tags in
# charts/lolday/values.yaml match each other AND the Chart.yaml
# `version` / `appVersion`. Catches the half-bumped release pattern
# (see docs/runbooks/deploy.md §release flow): v0.18.0 shipped with
# frontend tag still at v0.17.0 because Chart.yaml + backend tag were
# bumped while frontend tag was forgotten.
#
# Usage:
#   bash scripts/check-image-tags-aligned.sh
#
# Exit codes:
#   0 — Chart.yaml version + appVersion + both image tags aligned
#   1 — divergence detected; remediation printed to stderr

set -euo pipefail

# REPO_ROOT defaults to the repo containing this script. Tests override
# via LOLDAY_REPO_ROOT_OVERRIDE so they can point at a fixture repo
# under /tmp; mirrors scripts/build-helpers.sh's convention.
REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CHART_DIR="$REPO_ROOT/charts/lolday"
VALUES="$CHART_DIR/values.yaml"
CHART="$CHART_DIR/Chart.yaml"

chart_version=$(awk '/^version:/ {print $2; exit}' "$CHART" | tr -d '"')
chart_appversion=$(awk '/^appVersion:/ {print $2; exit}' "$CHART" | tr -d '"')

backend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-backend:" "$VALUES" \
  | head -1 | sed -E 's|.*lolday-backend:([^[:space:]#]+).*|\1|')
frontend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-frontend:" "$VALUES" \
  | head -1 | sed -E 's|.*lolday-frontend:([^[:space:]#]+).*|\1|')

if [ -z "$chart_version" ] || [ -z "$chart_appversion" ] || [ -z "$backend_tag" ] || [ -z "$frontend_tag" ]; then
  {
    echo "ERROR: could not parse one of the four expected fields:"
    echo "  Chart.yaml version    = '$chart_version'"
    echo "  Chart.yaml appVersion = '$chart_appversion'"
    echo "  values.yaml backend   = '$backend_tag'"
    echo "  values.yaml frontend  = '$frontend_tag'"
  } >&2
  exit 1
fi

expected="v$chart_version"
fail=0

if [ "$chart_appversion" != "$chart_version" ]; then
  echo "ERROR: Chart.yaml appVersion ($chart_appversion) != version ($chart_version)" >&2
  fail=1
fi
if [ "$backend_tag" != "$expected" ]; then
  echo "ERROR: backend image tag $backend_tag != expected $expected (from Chart.yaml version $chart_version)" >&2
  fail=1
fi
if [ "$frontend_tag" != "$expected" ]; then
  echo "ERROR: frontend image tag $frontend_tag != expected $expected (from Chart.yaml version $chart_version)" >&2
  fail=1
fi

if [ "$fail" -eq 1 ]; then
  cat >&2 <<'EOF'

Release commits must bump four fields together:
  charts/lolday/Chart.yaml   version
  charts/lolday/Chart.yaml   appVersion (tracks version)
  charts/lolday/values.yaml  backend.image  tag (must be v$version)
  charts/lolday/values.yaml  frontend.image tag (must be v$version)

See docs/runbooks/deploy.md §release flow.
EOF
  exit 1
fi

echo "image tags aligned with Chart.yaml: $expected"
