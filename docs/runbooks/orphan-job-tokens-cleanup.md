# Orphan job-token Secrets cleanup (#175)

One-shot operator verification after the multi-namespace reconciler
sweep ships. Spec context:
`docs/phase-history/2026-05-15-security-post-program-review.md` §#175.

## Background

Detector job pods authenticate to backend internal callbacks via a
short-lived `job-token-<uuid16>` Secret. Volcano vcjobs own the Secret
via `ownerReferences`, so the K8s GC controller normally cleans up when
the vcjob is deleted normally. The exception path is
`kubectl delete vcjob ... --grace-period=0 --force`, which bypasses
finalizers and the GC controller, leaving the Secret as an orphan.

The reconciler runs `reconcile_orphan_token_secrets` every iteration to
clean these up by age + liveness. Pre-#175 the sweep was scoped to a
single namespace (`JOB_NAMESPACE`). A 2026-05-05 migration moved live
vcjob traffic from `lolday` to `lolday-jobs` but the sweep stayed scoped
to the new namespace, leaving **718 stale Secrets** in `lolday`.

## Fix

`reconcile_orphan_token_secrets` now sweeps
`[JOB_NAMESPACE, *JOB_TOKEN_LEGACY_NAMESPACES]`. The legacy list is
configurable so a future ns migration can register the previous ns
without a code change.

Operator action: after `helm upgrade` lands the new backend image, set
`JOB_TOKEN_LEGACY_NAMESPACES=lolday` (whitespace-separated, mirrors the
`FERNET_KEYS` env parsing pattern) and re-roll the backend pod.

## Verification

After two minutes of reconciler runtime (one iteration period plus a
margin):

```bash
# Live ns should not accumulate stale tokens
kubectl get secret -n lolday-jobs --no-headers | grep -c '^job-token-' || true

# Legacy ns count should drop from 718 -> 0 over a few iterations
kubectl get secret -n lolday --no-headers | grep -c '^job-token-' || true
```

Reconciler metric (Prometheus, `kps-prometheus` port-forward):

```promql
rate(lolday_backend_errors_total{stage="orphan_token_secret_delete"}[5m])
```

Should remain at 0. A non-zero rate means K8s 4xx/5xx on Secret delete
(audit RBAC or namespace-not-found); investigate before retrying.

## Rollback

If the multi-namespace sweep deletes a Secret it should not have
(unlikely -- liveness check still gates on the live vcjob short-id), the
in-flight job pod that referenced it will fail its next internal
callback. The job's owner sees the failure surfaced as a job-event entry
and the reconciler marks the job FAILED on the next iteration. Recovery
is to re-submit the job.
