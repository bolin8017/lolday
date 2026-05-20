#!/usr/bin/env bash
set -euo pipefail

# Static check: every public-facing pod (a Service reached via the lolday
# Traefik IngressRoute) must have a NetworkPolicy ingress allow naming
# `kube-system + app.kubernetes.io/name=traefik` on its POD-side port.
#
# WHY: P1 H-25 (commit 06715ef) shipped backend-metrics-from-monitoring-only
# with `from: cloudflared@lolday`, but cloudflared dials the Traefik Service
# (see charts/lolday/templates/cloudflared.yaml ingress config) — never
# backend pods directly. Frontend had no allow rule at all. The platform
# 502'd silently for ~16h (helm rev 163 -> 173) because cookieless requests
# still 302 at the CF edge (masking origin reachability) and lab cached
# SPA sessions did not reload during the window. See:
#   - docs/runbooks/troubleshooting.md "HTTP 502 from Cloudflare edge..."
#   - PR #152
#
# Operates on `helm template` output — runs offline, no live cluster needed.
# Wire into deploy.sh as a pre-flight (before helm upgrade) so a bad chart
# fails fast.
#
# To register a new public-facing pod (anything routed via templates/ingress.yaml):
# extend the TARGETS table below with the NetworkPolicy name, the pod's
# matchLabels selector, and the POD-side port (kube-router enforces NP
# AFTER kube-proxy DNAT — Service port is wrong; use POD targetPort).

CHART_DIR="${1:-$(cd "$(dirname "$0")/../charts/lolday" && pwd)}"

if [ ! -d "$CHART_DIR" ]; then
  echo "ERROR: chart dir not found: $CHART_DIR" >&2
  exit 1
fi

# Helm requires non-empty values for several `required` template helpers even
# when we only care about NetworkPolicy shape. Pass placeholders — the rendered
# output is parsed in-memory and never applied.
RENDERED=$(mktemp)
trap 'rm -f "$RENDERED"' EXIT

# Limit rendering to our own NetworkPolicy templates. This keeps the parser
# off sub-chart YAML (loki / kps / volcano have stray tabs that PyYAML
# rejects) and is also faster. Add a --show-only line below if a new
# user-facing NetworkPolicy is added in a different template file.
helm template lolday "$CHART_DIR" -n lolday \
  --show-only templates/network-policy.yaml \
  --show-only templates/netpol-lolday-default-deny.yaml \
  --set monitoring.postgresExporter.password=x \
  --set monitoring.grafana.adminPassword=x \
  --set redis.auth.password=x \
  --set mlflow.db.password=x \
  --set backend.harborAdminPassword=x \
  --set cloudflare.tunnelToken=x \
  --set backend.fernetKeys=x \
  --set postgresql.auth.password=x \
  >"$RENDERED" 2>/dev/null

# The check logic lives in scripts/lib/np_check.py per R6 (scripts-and-ops.md
# §R6). Run from the repo root so `-m scripts.lib.np_check` resolves the
# package; the module reads the rendered YAML and prints OK / FAIL itself.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
python3 -m scripts.lib.np_check check "$RENDERED"
