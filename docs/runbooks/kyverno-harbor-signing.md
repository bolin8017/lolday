# Kyverno Harbor image signing (operator runbook)

> **Scope:** Bootstrap and operate the cosign-key-based supply-chain gate
> for `harbor.lolday.svc:80/lolday/*` images.
>
> **Closes:** issue #171 — option 2 (sign Harbor pushes with a key).
>
> **Sister policy:** `verify-lolday-image-signatures` (GHCR / keyless,
> covered by `docs/runbooks/kyverno-bootstrap.md`).

## Why

`docs/runbooks/kyverno-bootstrap.md` §"What Kyverno does in lolday" notes
the existing `verify-lolday-image-signatures` ClusterPolicy is pinned to
the GHCR workflow identity:

```yaml
imageReferences:
  - "ghcr.io/bolin8017/lolday-*"
```

Every long-running production Deployment (`backend`, `frontend`,
`mlflow-server`, `lolday-postgres`, `lolday-redis`, helpers consumed by
vcjobs) references **`harbor.lolday.svc:80/lolday/*`**, not GHCR. The
admission gate therefore gates **zero chart-managed Deployments** — it
only fires for test pods that happen to reference GHCR directly. Post-
program review surfaced this in §3.12.

This runbook closes the gap with the
`verify-lolday-harbor-image-signatures` ClusterPolicy + a key-based
cosign attestor.

## Setup (once per cluster)

```bash
# 1. Bootstrap the cosign keypair. Generates ~/.cosign/lolday-harbor.{key,pub},
#    installs the public half as Secret kyverno/cosign-harbor-pubkey.
bash scripts/cosign-harbor-init.sh
#    Prompts for a private-key password. Store it in your password manager.
#    Idempotent — re-running with an existing keypair short-circuits.

# 2. Verify the Secret landed.
kubectl -n kyverno get secret cosign-harbor-pubkey \
  -o jsonpath='{.data.cosign\.pub}' | base64 -d
#    Expected: a PEM-encoded P-256 ECDSA public key (-----BEGIN PUBLIC KEY-----).

# 3. Verify the ClusterPolicy is loaded + ready.
kubectl get clusterpolicy verify-lolday-harbor-image-signatures \
  -o jsonpath='{.status.ready}{"\n"}'
#    Expected: `true`.

# 4. Verify the policy starts in Audit mode (recommended initial state).
kubectl get clusterpolicy verify-lolday-harbor-image-signatures \
  -o jsonpath='{.spec.validationFailureAction}{"\n"}'
#    Expected: `Audit`.
```

## Daily flow

`scripts/build-helpers.sh` signs every Harbor push by digest after the
push completes. No additional operator action is required during routine
helper releases:

```bash
bash scripts/build-helpers.sh
# [build] build-helper -> harbor.lolday.svc:80/lolday/build-helper:<sha>
# [sign]  build-helper @ sha256:abc123...
```

Per-helper-release detail: `docs/runbooks/release-helpers.md`.

### Manually-pushed Harbor images

For pushes that do NOT flow through `build-helpers.sh` (the
`mlflow-server`, `pytorch-cu12-base`, and any operator-driven backend /
frontend image build), sign the push manually with the same key:

```bash
# Replace <name>, <tag>, <digest>:
docker push harbor.lolday.svc.cluster.local:80/lolday/<name>:<tag>
DIGEST=$(docker buildx imagetools inspect --raw \
  harbor.lolday.svc.cluster.local:80/lolday/<name>:<tag> \
  | sha256sum | awk '{print "sha256:" $1}')
# (Or pull the digest from the Harbor UI / Harbor API as build-helpers.sh does.)

cosign sign \
  --yes --tlog-upload=false \
  --key ~/.cosign/lolday-harbor.key \
  harbor.lolday.svc:80/lolday/<name>@${DIGEST}
```

Document the operator-driven manual flow in the same release note that
records the image bump.

### Detector BuildKit signing (tech-debt)

Detector vcjobs that build via in-cluster BuildKit push their built
artefact to Harbor automatically — currently UNSIGNED. Captured in
`docs/architecture.md` §10 as a tech-debt item. Two candidate fixes:

1. **BuildKit post-push hook** — extend the BuildKit Job spec to run
   `cosign sign --identity-token` against the K8s service-account JWT
   (keyless, no key custody on cluster). Mainstream pattern for
   in-cluster signing.
2. **`maldet` framework sign step** — push the responsibility upstream
   to `maldet`'s build runner so every detector repo bakes the
   signature into the build pipeline.

Decision deferred until a follow-up spec under
`docs/superpowers/specs/`.

## Promotion from Audit to Enforce

The initial policy state is `validationFailureAction: Audit` — every
unsigned push records a PolicyReport but is admitted. Run for 7 days
to surface any in-cluster images we missed, then promote.

```bash
# 1. Audit for ≥ 7 days.
#    Look for unsigned image admissions in PolicyReports:
kubectl get policyreports.wgpolicyk8s.io -A \
  -l 'policy.kyverno.io/policy-name=verify-lolday-harbor-image-signatures' \
  -o json | jq '.items[] | {ns: .metadata.namespace,
                            failed: [.results[]? | select(.result == "fail") | .resources[]?.name]}'
#    Expected after a clean week: empty failed[] for every report.

# 2. Promote in git via the chart values flag (plumbed 2026-05-19,
#    §10 #25(b)). Edit charts/lolday/values.yaml:
#      kyverno:
#        harborImageSignatureEnforce: true   # was false
#    Then redeploy:
bash scripts/deploy.sh

# 3. Smoke-test: an unsigned pod must be rejected.
kubectl run sig-test --rm -i --restart=Never \
  --image=harbor.lolday.svc:80/lolday/no-such-image:t1 \
  --namespace=lolday --dry-run=server -o yaml
#    Expected: Kyverno admission error mentioning
#    `verify-lolday-harbor-image-signatures`.
```

Rollback path: revert the values flag to `false` and `bash scripts/deploy.sh`
again — the template re-renders `validationFailureAction: Audit` and
admissions revert to "record-only".

If you need to flip in an emergency without a redeploy round-trip
(rare), the runtime equivalent is still available:

```bash
kubectl patch clusterpolicy verify-lolday-harbor-image-signatures \
  --type=merge -p '{"spec":{"validationFailureAction":"Enforce"}}'
```

The next `helm upgrade` will reconcile back to whatever the chart value
says — so update `values.yaml` to match BEFORE the next deploy, or the
flip silently reverts.

## Key rotation

Cadence: **yearly OR on compromise**. Mainstream cosign hygiene.

```bash
# 1. Rotate. Generates a NEW keypair, moves the OLD keypair aside with
#    suffix .pre-rotate-<unix-ts>, replaces the K8s Secret in-place.
bash scripts/cosign-harbor-init.sh --force-new

# 2. Re-sign all images currently referenced in helpers.lock under the
#    NEW key. Existing signatures under the OLD key remain valid until
#    the matching image tags are re-pushed; new admissions verify against
#    the NEW key only.
bash scripts/build-helpers.sh
#    The script re-signs all helper images by digest. Backend / frontend
#    / mlflow-server / pytorch-cu12-base must be re-signed manually
#    (see "Manually-pushed Harbor images" above).

# 3. Verify the new pubkey is the one Kyverno is using.
kubectl -n kyverno get secret cosign-harbor-pubkey \
  -o jsonpath='{.data.cosign\.pub}' | base64 -d \
  | diff - ~/.cosign/lolday-harbor.pub
#    Expected: no output (identical).

# 4. After all production images are re-signed, archive the old keypair
#    or destroy it per your incident-response policy. Do NOT delete
#    pre-rotation files until step 2 has fully replayed.
ls -la ~/.cosign/lolday-harbor.{key,pub}.pre-rotate-*
```

## Verification: end-to-end check

```bash
# Confirm the round trip: signed harbor image is admitted; unsigned one is not.
# (Run this after Enforce promotion.)
kubectl run sig-test-positive --rm -i --restart=Never \
  --image=$(jq -r .build_helper charts/lolday/helpers.lock) \
  --namespace=lolday --dry-run=server -o yaml | head
# Expected: no Kyverno error.

kubectl run sig-test-negative --rm -i --restart=Never \
  --image=harbor.lolday.svc:80/lolday/build-helper:not-a-real-tag \
  --namespace=lolday --dry-run=server -o yaml
# Expected: admission denied with reference to the verify-lolday-harbor-...
# ClusterPolicy.
```

## Audit-trail

- Issue: #171 (option 2 chosen)
- Policy: `charts/lolday/templates/policies/verify-images-harbor.yaml`
- Bootstrap script: `scripts/cosign-harbor-init.sh`
- Sign step: `scripts/build-helpers.sh::cosign_sign`
- Sister policy + Kyverno install gotchas: `docs/runbooks/kyverno-bootstrap.md`

## Out of scope (tracked)

- **Detector BuildKit signing.** vcjob-built detector artefacts pushed
  to Harbor are unsigned. Tech-debt §10.
- **External KMS for the private key.** Today the private key lives on
  the operator workstation. A KMS-backed signing flow (e.g. cosign's
  `--key gcpkms://...` / `awskms://...` providers) reduces key-custody
  burden. Out of scope for a single-operator lab; revisit when the team
  grows.
