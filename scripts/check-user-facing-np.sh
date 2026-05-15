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

python3 - "$RENDERED" <<'PY'
import sys
import yaml

# Each tuple: (NetworkPolicy name, expected pod-selector matchLabels, POD-side port)
# Add a new row when a new public-facing pod is exposed via Traefik IngressRoute.
TARGETS = [
    ("backend-metrics-from-monitoring-only", {"app.kubernetes.io/component": "backend"}, 8000),
    ("frontend-ingress-allow",               {"app": "frontend"},                        8080),
    ("mlflow-ingress-allow",                 {"app.kubernetes.io/component": "mlflow"},  5000),
]

TRAEFIK_NS_LABEL = ("kubernetes.io/metadata.name", "kube-system")
TRAEFIK_POD_LABEL = ("app.kubernetes.io/name", "traefik")

with open(sys.argv[1]) as f:
    docs = [d for d in yaml.safe_load_all(f) if d and d.get("kind") == "NetworkPolicy"]
nps = {d["metadata"]["name"]: d for d in docs}

fails = []
for name, want_selector, want_port in TARGETS:
    if name not in nps:
        fails.append(f"NetworkPolicy/{name}: not rendered by helm template")
        continue
    np = nps[name]
    spec = np.get("spec") or {}
    got_selector = (spec.get("podSelector") or {}).get("matchLabels") or {}
    if got_selector != want_selector:
        fails.append(
            f"NetworkPolicy/{name}: podSelector matchLabels={got_selector!r} "
            f"!= expected {want_selector!r}"
        )
        continue
    found = False
    for rule in (spec.get("ingress") or []):
        ports_ok = any(
            (p.get("port") == want_port and (p.get("protocol") or "TCP") == "TCP")
            for p in (rule.get("ports") or [])
        )
        if not ports_ok:
            continue
        for src in (rule.get("from") or []):
            ns_labels = (src.get("namespaceSelector") or {}).get("matchLabels") or {}
            pod_labels = (src.get("podSelector") or {}).get("matchLabels") or {}
            if (
                ns_labels.get(TRAEFIK_NS_LABEL[0]) == TRAEFIK_NS_LABEL[1]
                and pod_labels.get(TRAEFIK_POD_LABEL[0]) == TRAEFIK_POD_LABEL[1]
            ):
                found = True
                break
        if found:
            break
    if not found:
        fails.append(
            f"NetworkPolicy/{name}: no ingress rule allows from "
            f"kube-system + app.kubernetes.io/name=traefik on POD-side port {want_port}/TCP"
        )

if fails:
    print("FAIL: user-facing pods missing kube-system+traefik ingress allow:", file=sys.stderr)
    for line in fails:
        print(f"  - {line}", file=sys.stderr)
    print(
        "  See docs/runbooks/troubleshooting.md "
        "'HTTP 502 from Cloudflare edge with valid CF Access JWT'.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"  OK: {len(TARGETS)} user-facing pod(s) have kube-system+traefik ingress allow.")
PY
