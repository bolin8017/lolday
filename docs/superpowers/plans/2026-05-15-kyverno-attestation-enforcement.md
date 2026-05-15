# Kyverno SLSA provenance attestation enforcement — staged rollout

> Follow-up to #170. Tracks the second half of the SLSA L2 → L3 lift: making
> Kyverno require a SLSA build provenance attestation in addition to the
> existing Sigstore signature.

## Why this is a separate PR

The companion CI change (`.github/actions/docker-meta-build/action.yml` —
`actions/attest-build-provenance` step) ships attestations on every new
GHCR push. **Existing prod images do NOT have attestations.** If we enable
Kyverno enforcement in the same PR, every running pod fails admission on the
next restart and the cluster goes down.

Mainstream SLSA staged-rollout pattern:

1. **Ship the generator first.** Land CI changes; verify a fresh image gets an
   attestation; let the catalogue of attested digests grow.
2. **Rebuild all in-cluster images** so the digests Kyverno checks against
   carry an attestation.
3. **Flip enforcement.** Update the Kyverno policy to require
   `attestations:` alongside `attestors:`.

This file tracks step 3 — to be executed after at least one full pass of
`build-helpers.sh` + image rebuilds (`scripts/build-helpers.sh` for
build-helper + job-helper; backend / frontend via `gh workflow run images.yml`)
plus a re-pull cycle (`scripts/deploy.sh`) so the cluster runs only
attested digests.

## Pre-conditions to flip enforcement

- [ ] At least one green run of `gh workflow run images.yml` after this PR's
      CI change merged.
- [ ] `gh attestation list --owner bolin8017 --repo lolday` returns an
      entry for every image short name in the matrix.
- [ ] `gh attestation verify --owner bolin8017 oci://ghcr.io/bolin8017/lolday-backend@sha256:<digest>` returns success.
- [ ] All four production images (backend, frontend, build-helper,
      job-helper) have been rebuilt + redeployed via the standard flow
      (`docs/runbooks/release-helpers.md` + `docs/runbooks/deploy.md`).
- [ ] No pods in `lolday` / `lolday-jobs` carry pre-attestation digests
      (`kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.spec.containers[*].image}{"\n"}{end}' | grep ghcr.io/bolin8017`).

## Step 3: chart change

Extend `charts/lolday/templates/policies/verify-images.yaml` — add an
`attestations:` block under the existing `verifyImages` rule, mirroring the
four-keyless-entry structure used in `attestors:`. Predicate type:
`https://slsa.dev/provenance/v1` (Kyverno 1.13+; verify chart-vendored
Kyverno version with `kubectl get clusterpolicy verify-lolday-image-signatures -o yaml | grep apiVersion`
to confirm predicate compatibility before flipping).

Sketch:

```yaml
attestations:
  - type: https://slsa.dev/provenance/v1
    attestors:
      - count: 1
        entries:
          - keyless:
              subject: "https://github.com/bolin8017/lolday/.github/workflows/images.yml@refs/heads/main"
              issuer: "https://token.actions.githubusercontent.com"
              rekor:
                url: "https://rekor.sigstore.dev"
          - keyless:
              subjectRegExp: "^https://github\\.com/bolin8017/lolday/\\.github/workflows/images\\.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$"
              issuer: "https://token.actions.githubusercontent.com"
              rekor:
                url: "https://rekor.sigstore.dev"
          - keyless:
              subject: "https://github.com/bolin8017/lolday/.github/workflows/helpers.yml@refs/heads/main"
              issuer: "https://token.actions.githubusercontent.com"
              rekor:
                url: "https://rekor.sigstore.dev"
          - keyless:
              subjectRegExp: "^https://github\\.com/bolin8017/lolday/\\.github/workflows/helpers\\.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$"
              issuer: "https://token.actions.githubusercontent.com"
              rekor:
                url: "https://rekor.sigstore.dev"
```

## Rollback

`failurePolicy: Fail` means a buggy enforcement policy can wedge admissions
cluster-wide. Mitigations:

1. **Canary first.** Set `validationFailureAction: Audit` in a feature
   branch, deploy, watch `kyverno_policy_results_total{action="fail"}` in
   Grafana for 24h. Only flip to `Enforce` once the count is zero.
2. **Pre-tested rollback PR.** Keep a revert PR ready that drops the
   `attestations:` block. If admissions break post-flip, fast-merge the
   revert; existing images keep working.
3. **Operator break-glass.** `kubectl delete clusterpolicy verify-lolday-image-signatures`
   removes the entire policy (loses signature verification too — last
   resort).

## References

- This PR's spec: SLSA L3 generator landed in `.github/actions/docker-meta-build/action.yml`.
- Sigstore baseline: `charts/lolday/templates/policies/verify-images.yaml`.
- Kyverno docs on `verifyImages` `attestations:` predicate matching:
  https://kyverno.io/docs/writing-policies/verify-images/sigstore/
- SLSA v1 predicate spec: https://slsa.dev/spec/v1.0/provenance
