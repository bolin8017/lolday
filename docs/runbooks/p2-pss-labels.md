# Pod Security Standards labels for lolday namespaces

Source: [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md) §6.2 finding **H-14**.

## What the chart handles

`charts/lolday/templates/jobs-namespace.yaml` (P2 commit) labels the
`lolday-jobs` namespace at:

- `pod-security.kubernetes.io/audit: restricted` — admission log on violations
- `pod-security.kubernetes.io/warn: restricted` — kubectl warn on violations
- `pod-security.kubernetes.io/enforce: baseline` — reject privileged/hostPath/hostNetwork pods, but allow Unconfined seccomp (needed by BuildKit until the custom profile from P2 T12 is verified across all builds)

## What the operator must apply manually

The `lolday` and `monitoring` namespaces are created by `helm install --create-namespace`
and are not chart-rendered Namespace objects. After `bash scripts/deploy.sh`,
apply:

```bash
kubectl label ns lolday \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/enforce=baseline \
  --overwrite

kubectl label ns monitoring \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/enforce=baseline \
  --overwrite
```

`baseline` is conservative for the initial roll-out: it blocks the worst
offenders (privileged, hostPath, hostNetwork, hostPID, hostIPC) but is
permissive on capabilities and seccomp, which lets MinIO, Harbor,
postgres-exporter, and other sub-chart pods that haven't all opted into
`Restricted` still run.

## Audit window

For 7 days after deploy, observe PodSecurity audit/warn events:

```bash
kubectl get events --all-namespaces --field-selector reason=FailedCreate | grep PodSecurity
kubectl get events --all-namespaces | grep -i "violates PodSecurity"
```

If you see warnings for pods you authored (i.e. lolday's own
templates/sub-charts), fix the pod's securityContext to match Restricted
before the next step. Most P2 work has already made the lolday templates
PSS-Restricted-compatible — the warnings should come from sub-charts.

## Promotion to enforce=restricted

After 7 days of clean audit logs, run:

```bash
kubectl label ns lolday \
  pod-security.kubernetes.io/enforce=restricted --overwrite

kubectl label ns monitoring \
  pod-security.kubernetes.io/enforce=restricted --overwrite
```

**Do NOT promote `lolday-jobs` to `enforce: restricted` until BuildKit moves
to its own namespace (Phase 2 follow-up). BuildKit's `seccompProfile:
Unconfined` (or the custom Localhost profile from T12) is incompatible with
Restricted.**

## Rollback

If `enforce=restricted` blocks a legitimate pod:

```bash
kubectl label ns <name> pod-security.kubernetes.io/enforce=baseline --overwrite
```

The pod will be admitted; audit/warn still flags it so the operator knows
which template needs to be hardened.

## See also

- [`docs/superpowers/plans/2026-05-12-security-hardening-p2-workload-identity.md`](../superpowers/plans/2026-05-12-security-hardening-p2-workload-identity.md) — the implementing plan
- [Kubernetes documentation: Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
