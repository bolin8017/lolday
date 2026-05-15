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

### 2. lolday — primary infra ns

```bash
kubectl label ns lolday pod-security.kubernetes.io/enforce=restricted --overwrite
```

Verify no pods are rejected:

```bash
kubectl -n lolday get events --field-selector reason=FailedCreate
```

If a pod is rejected, roll back immediately:

```bash
kubectl label ns lolday pod-security.kubernetes.io/enforce=baseline --overwrite
```

then fix the offending pod's securityContext at source.

### 3. lolday-jobs — vcjob + build Jobs ns

After lolday is stable at restricted for 24h:

```bash
kubectl label ns lolday-jobs pod-security.kubernetes.io/enforce=restricted --overwrite
```

This works only after the buildkit-seccomp-installer move (commit 2 of
PR `fix/chart-hardening`). Before that move, BuildKit lived in
`lolday-jobs` and needed enforce=baseline. After the move, build pods
in `lolday-jobs` use the Localhost seccomp profile and meet restricted.

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
