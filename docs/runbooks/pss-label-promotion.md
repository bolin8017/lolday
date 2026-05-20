# PSS label promotion (post chart-hardening 0.24.0)

Source: post-program follow-up #174 (H-14). Continuation of
[`p2-pss-labels.md`](p2-pss-labels.md). Chart change in PR `fix/chart-hardening`
(chart 0.24.0).

## What the chart now handles

After this PR ships:

- `charts/lolday/templates/builds-namespace.yaml` creates the new
  `lolday-builds` namespace at
  `pod-security.kubernetes.io/{audit,warn}=restricted` and
  `enforce=baseline`. The buildkit-seccomp-installer DaemonSet
  (formerly in `lolday`) moves here so it can keep running with
  `runAsUser: 0` + `CAP_CHOWN/DAC_OVERRIDE/FOWNER`.
- `scripts/deploy.sh` applies `audit/warn=restricted` to the `lolday`,
  `monitoring`, and `trivy-system` namespaces on every deploy. The
  `lolday-jobs` ns already carries `audit/warn=restricted` +
  `enforce=baseline` via `templates/jobs-namespace.yaml` (P2).

Result: every lolday-owned namespace audits/warns at restricted. No
namespace is at enforce=restricted yet — that promotion is what this
runbook covers.

## Observation window (3 days)

Before promoting any ns to `enforce=restricted`, observe the
PodSecurity audit/warn signal:

```bash
# Past 3 days of PodSecurity admission events (audit + warn).
kubectl get events --all-namespaces \
  --field-selector reason=PodSecurity \
  --sort-by .lastTimestamp

# Pods admitted with PSS audit-mode flags (the audit annotation lands on
# the pod itself).
kubectl get pods --all-namespaces \
  -o jsonpath='{range .items[?(@.metadata.annotations.pod-security\.kubernetes\.io/audit-violations)]}{.metadata.namespace}/{.metadata.name}: {.metadata.annotations.pod-security\.kubernetes\.io/audit-violations}{"\n"}{end}'
```

If you see violations attributable to lolday templates or sub-charts you
own, fix the securityContext at source before promoting. If they come
from a sub-chart you do not control (e.g. an upstream init container
in trivy-operator), open a ticket and either patch via a chart values
override or keep the ns at enforce=baseline.

## Promotion sequence

Promote one ns at a time, in increasing risk order. After each, watch
for 24h before the next.

### 1. lolday-builds — stays at enforce=baseline

The seccomp installer is the only pod in `lolday-builds` and it
requires elevated caps. Do NOT promote `lolday-builds` to restricted.

### 2. lolday — stays at enforce=baseline (intrinsic, do NOT promote)

The 2026-05-18 audit (`docs/architecture.md` §10 #36) established that two
in-ns DaemonSets are intrinsically incompatible with PSS `restricted`:

- **`lolday-alloy`** — log collection from `/var/log/pods/*.log` requires
  the hostPath volumes `/var/log` and `/var/lib/docker/containers`;
  hostPath volumes are forbidden under `restricted` by design.
- **`lolday-prometheus-node-exporter`** — host-metric collection requires
  `hostNetwork` / `hostPID` / `hostPort` + the `/host/{proc,sys,root}`
  hostPath volumes; same restriction.

Both pods received defense-in-depth hardening (PRs #455, #456 — capability
drop + no privilege escalation + `RuntimeDefault` seccomp at pod and
container level), so the gap between `baseline` and `restricted` for
these two pods is intrinsic infrastructure-coupling, not behavioural
laxity.

Operator options if `restricted` enforcement on the `lolday` ns becomes
a hard requirement (e.g. external compliance audit):

- **(a) Relocate the two DaemonSets to a side-ns** (`monitoring-host` or
  similar) kept at `baseline`, then promote `lolday` to `restricted`.
  Requires editing the chart to override the alloy + node-exporter
  sub-chart deployment ns (alloy via `controller.nodeSelector`,
  node-exporter via `namespaceOverride`) and re-wiring ServiceMonitors.
  Largest blast radius; consider a spec.
- **(b) Per-pod waiver via `pod-security.kubernetes.io/enforce-version: latest`**
  on the two DaemonSets, then promote the ns. K8s 1.32+ supports
  per-pod opt-out — see [the Pod Security Admission docs](https://kubernetes.io/docs/concepts/security/pod-security-admission/#namespace-labels).
- **(c) Stay at `baseline` permanently** (current recommendation). The
  defense-in-depth hardening on every in-ns pod already approximates
  `restricted` for everything except the two intrinsic outliers.

If a future operator chooses (a) or (b), update this runbook AND
`docs/architecture.md` §10 #36 to reflect the new state before applying.

### 3. lolday-jobs — already at enforce=restricted (since 2026-05-18)

The `lolday-jobs` ns was promoted to `enforce=restricted` on 2026-05-18
when issue #186 closed. The promotion was safe because:

- BuildKit moved out to `lolday-builds` (chart 0.24.0, 2026-05-15).
- The remaining workloads (vcjob detector containers) use the Localhost
  seccomp profile and meet `restricted`.

The chart template `templates/jobs-namespace.yaml` now pins
`pod-security.kubernetes.io/enforce: restricted`; the helm-unittest
invariant in `charts/lolday/tests/pss_test.yaml` ("lolday-jobs namespace
enforces restricted PSS") flips red on any regression. No operator
action needed — this section is informational.

### 4. monitoring + trivy-system — external sub-charts

These run pod specs that lolday does not author. Verify the upstream
chart's pod specs meet restricted before promoting:

```bash
# Are there any pods missing seccompProfile or runAsNonRoot?
for ns in monitoring trivy-system; do
  echo "=== $ns ==="
  kubectl -n "$ns" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.securityContext.runAsNonRoot}{"\t"}{.spec.securityContext.seccompProfile.type}{"\n"}{end}'
done
```

If clean, promote one at a time:

```bash
kubectl label ns monitoring pod-security.kubernetes.io/enforce=restricted --overwrite
kubectl label ns trivy-system pod-security.kubernetes.io/enforce=restricted --overwrite
```

## Rollback

`enforce=restricted` is reversible at any time:

```bash
kubectl label ns <name> pod-security.kubernetes.io/enforce=baseline --overwrite
```

The audit + warn labels stay so violations remain visible.

## See also

- [`p2-pss-labels.md`](p2-pss-labels.md) — original P2 ramp doc
- [`docs/phase-history/2026-05-14-security-audit-findings.md`](../phase-history/2026-05-14-security-audit-findings.md) — H-14 finding context
- [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
