#!/usr/bin/env bash
# Two assertions:
#   1. Chart.yaml version + appVersion + lolday-backend / lolday-frontend
#      image tags are all aligned (catches half-bumped release).
#   2. Every `image:` line in values.yaml ends in @sha256:<64 hex>
#      (H-21-img: digest pin is mandatory for content-addressable refs).
#
# Usage:
#   bash scripts/check-image-tags-aligned.sh
#
# Exit codes:
#   0 — both assertions pass
#   1 — divergence detected; remediation printed to stderr

set -euo pipefail

REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CHART_DIR="$REPO_ROOT/charts/lolday"
VALUES="$CHART_DIR/values.yaml"
CHART="$CHART_DIR/Chart.yaml"

# ---- Pass 1: tag alignment (existing behavior, unchanged) ----
chart_version=$(awk '/^version:/ {print $2; exit}' "$CHART" | tr -d '"')
chart_appversion=$(awk '/^appVersion:/ {print $2; exit}' "$CHART" | tr -d '"')

# Match `image: harbor.lolday.svc:80/lolday/lolday-{backend,frontend}:vX.Y.Z[@sha256:...]`
backend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-backend:" "$VALUES" \
  | head -1 | sed -E 's|.*lolday-backend:([^@[:space:]#]+).*|\1|')
frontend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-frontend:" "$VALUES" \
  | head -1 | sed -E 's|.*lolday-frontend:([^@[:space:]#]+).*|\1|')

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
  echo "ERROR: backend image tag $backend_tag != expected $expected" >&2
  fail=1
fi
if [ "$frontend_tag" != "$expected" ]; then
  echo "ERROR: frontend image tag $frontend_tag != expected $expected" >&2
  fail=1
fi

# ---- Pass 2: digest pin (NEW for H-21-img) ----
# Every `image:` scalar line in values.yaml pointing at a lolday-owned
# Harbor ref (harbor.lolday.svc:80/lolday/...) must end in
# @sha256:<64-hex>. Sub-chart refs (postgres, redis, cloudflared,
# postgres-exporter) are handled by T4 — leave their digest pinning
# out of scope for T1.
# T4 will widen this match.
while IFS= read -r line; do
  ref=$(echo "$line" | sed -E 's|^[[:space:]]*image:[[:space:]]+([^[:space:]#]+).*|\1|')
  if ! echo "$ref" | grep -qE '@sha256:[0-9a-f]{64}$'; then
    echo "ERROR: image ref missing @sha256:<64-hex> digest pin: $ref" >&2
    fail=1
  fi
done < <(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/" "$VALUES")

if [ "$fail" -eq 1 ]; then
  cat >&2 <<'EOF'

Release commits must:
  1. Bump Chart.yaml version + appVersion + values.yaml backend/frontend tags together.
  2. Pin every `image:` in values.yaml via @sha256:<digest>. Capture digests at release time:
       docker buildx imagetools inspect <ref> --format '{{.Manifest.Digest}}'

See docs/runbooks/deploy.md §release flow and docs/superpowers/specs/2026-05-12-security-hardening-design.md H-21-img.
EOF
  exit 1
fi

echo "image tags aligned with Chart.yaml: $expected; digest pin present on all values.yaml image refs"
