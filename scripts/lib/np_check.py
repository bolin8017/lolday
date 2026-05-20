"""Static check: user-facing pods must allow ingress from Traefik.

Phase 4 D4.5 R6 extraction. Replaces the inline ``python3 -<<'PY' ... PY``
heredoc that previously lived in ``scripts/check-user-facing-np.sh``.
The shell wrapper now calls::

    python3 -m scripts.lib.np_check check <rendered-yaml>

Verbs:
    check <path>     — parse helm-rendered NetworkPolicy YAML and verify
                       every TARGET row has an ingress rule allowing
                       ``kube-system + app.kubernetes.io/name=traefik``
                       on the declared POD-side port. Exit 0 clean,
                       1 on any failure, 2 on IO / parse error.

Why this exists: P1 H-25 (commit 06715ef) shipped backend-metrics ingress
keyed on ``cloudflared@lolday`` but cloudflared dials the Traefik Service,
not backend pods directly. Frontend had no allow rule at all. Result was
~16h of silent 502s (helm rev 163 → 173) before the regression surfaced.
This check runs against ``helm template`` output as a pre-deploy and
pre-commit gate so a bad chart never reaches the cluster.

NetworkPolicy enforcement under K3s kube-router happens AFTER kube-proxy
DNAT translation, so ``port:`` in the rule is matched against the POD
``targetPort``, never the Service ``port``. Each TARGETS row encodes the
POD-side number, not the Service one.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

import yaml

# (NetworkPolicy name, expected pod-selector matchLabels, POD-side port).
# Add a new row when a new public-facing pod is exposed via Traefik IngressRoute.
DEFAULT_TARGETS: tuple[tuple[str, Mapping[str, str], int], ...] = (
    (
        "backend-metrics-from-monitoring-only",
        {"app.kubernetes.io/component": "backend"},
        8000,
    ),
    ("frontend-ingress-allow", {"app": "frontend"}, 8080),
    ("mlflow-ingress-allow", {"app.kubernetes.io/component": "mlflow"}, 5000),
)

TRAEFIK_NS_LABEL = ("kubernetes.io/metadata.name", "kube-system")
TRAEFIK_POD_LABEL = ("app.kubernetes.io/name", "traefik")


def _load_network_policies(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        docs = [
            d for d in yaml.safe_load_all(f) if d and d.get("kind") == "NetworkPolicy"
        ]
    return {d["metadata"]["name"]: d for d in docs}


def _ingress_allows_traefik(np: dict, want_port: int) -> bool:
    """True iff ``np`` has an ingress rule that names kube-system + traefik
    on TCP/want_port (POD-side)."""
    for rule in np.get("spec", {}).get("ingress") or []:
        ports_ok = any(
            (p.get("port") == want_port and (p.get("protocol") or "TCP") == "TCP")
            for p in (rule.get("ports") or [])
        )
        if not ports_ok:
            continue
        for src in rule.get("from") or []:
            ns_labels = (src.get("namespaceSelector") or {}).get("matchLabels") or {}
            pod_labels = (src.get("podSelector") or {}).get("matchLabels") or {}
            if (
                ns_labels.get(TRAEFIK_NS_LABEL[0]) == TRAEFIK_NS_LABEL[1]
                and pod_labels.get(TRAEFIK_POD_LABEL[0]) == TRAEFIK_POD_LABEL[1]
            ):
                return True
    return False


def check_user_facing_np(
    rendered_yaml_path: str,
    targets: Sequence[tuple[str, Mapping[str, str], int]] = DEFAULT_TARGETS,
) -> list[str]:
    """Return a list of failure messages (empty = clean)."""
    nps = _load_network_policies(rendered_yaml_path)
    fails: list[str] = []
    for name, want_selector, want_port in targets:
        if name not in nps:
            fails.append(f"NetworkPolicy/{name}: not rendered by helm template")
            continue
        spec = nps[name].get("spec") or {}
        got_selector = (spec.get("podSelector") or {}).get("matchLabels") or {}
        if got_selector != dict(want_selector):
            fails.append(
                f"NetworkPolicy/{name}: podSelector matchLabels={got_selector!r} "
                f"!= expected {dict(want_selector)!r}"
            )
            continue
        if not _ingress_allows_traefik(nps[name], want_port):
            fails.append(
                f"NetworkPolicy/{name}: no ingress rule allows from "
                f"kube-system + app.kubernetes.io/name=traefik on POD-side port "
                f"{want_port}/TCP"
            )
    return fails


def _dispatch(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m scripts.lib.np_check <verb> [args...]", file=sys.stderr)
        return 2
    verb, *args = argv
    if verb != "check":
        print(f"unknown verb: {verb}", file=sys.stderr)
        return 2
    if not args:
        print("usage: check <rendered-yaml>", file=sys.stderr)
        return 2
    try:
        fails = check_user_facing_np(args[0])
    except FileNotFoundError as e:
        print(f"ERROR: rendered YAML missing: {e}", file=sys.stderr)
        return 2
    except (yaml.YAMLError, ValueError) as e:
        print(f"ERROR: failed to parse rendered YAML: {e}", file=sys.stderr)
        return 2
    if fails:
        print(
            "FAIL: user-facing pods missing kube-system+traefik ingress allow:",
            file=sys.stderr,
        )
        for line in fails:
            print(f"  - {line}", file=sys.stderr)
        print(
            "  See docs/runbooks/troubleshooting.md "
            "'HTTP 502 from Cloudflare edge with valid CF Access JWT'.",
            file=sys.stderr,
        )
        return 1
    print(
        f"  OK: {len(DEFAULT_TARGETS)} user-facing pod(s) have kube-system+traefik ingress allow."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return _dispatch(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
