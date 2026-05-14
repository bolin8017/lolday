# Kyverno bootstrap (runbook)

> **Scope:** Installing or upgrading the Kyverno admission controller as a
> sub-chart of `charts/lolday`. Captures the three edge cases discovered
> during the P4 ship (#139) so future Kyverno work doesn't re-discover them.
>
> **Spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md) §6.4 (P4 supply chain)
> **Audit-trail:** [`docs/phase-history/2026-05-14-security-audit-findings.md`](../phase-history/2026-05-14-security-audit-findings.md) → H-23-cluster
> **Postmortem:** [`docs/postmortems/2026-05-12-security-audit-program.md`](../postmortems/2026-05-12-security-audit-program.md) §4 (breaking-change inventory)

## What Kyverno does in lolday

- Cluster admission gate for `lolday` + `lolday-jobs` namespaces.
- `verifyImages` policy pinned to the GHCR workflow identity
  `https://github.com/bolin8017/lolday/.github/workflows/{images,helpers}.yml@refs/heads/main`
  (Cosign keyless trust root, per spec §8 D3).
- PSS baseline background audit (folds into the P2 PSS labels —
  Kyverno reports violations without blocking admission).

Kyverno's own controllers run in the `kyverno` namespace and are explicitly
**excluded from the verifyImages policy** so a Kyverno upgrade cannot reject
its own image during the rolling restart.

## The three bootstrap gotchas

### 1. CRDs do not fit in the release Secret (1 MiB cap)

**Symptom:** `helm install kyverno ...` fails with

```
rendered manifests contain a resource that already exists. Unable to continue
with install: ... cannot patch ... helm.sh/release-name: invalid
```

or the Secret it writes is rejected mid-install:

```
Secret "sh.helm.release.v1.kyverno.v1" is invalid: data: Too long: must have
at most 1048576 bytes
```

**Root cause.** Helm packs the full rendered chart (incl. all CRDs) into a
single Kubernetes Secret. Kyverno's CRDs are ~1.4 MiB together; the 1 MiB
Secret cap is a Helm 3 ceiling that cannot be raised at the Helm level.

**Fix.** Set `crds.install: false` in the umbrella values and apply CRDs
out-of-band BEFORE the helm operation. Carry Helm-ownership annotations on
the CRDs so the chart can adopt them on subsequent upgrades.

```bash
# 1. Apply CRDs from the Kyverno chart's own raw manifests (NOT helm install).
KYVERNO_VERSION=3.x.y    # match the version your sub-chart vendors
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/kyverno/kyverno/v${KYVERNO_VERSION}/config/crds/

# 2. Stamp Helm-ownership annotations so the chart sees them as managed.
for crd in $(kubectl get crd -l app.kubernetes.io/part-of=kyverno -o name); do
  kubectl annotate "$crd" \
    meta.helm.sh/release-name=lolday \
    meta.helm.sh/release-namespace=lolday \
    --overwrite
  kubectl label "$crd" \
    app.kubernetes.io/managed-by=Helm \
    --overwrite
done

# 3. Now the umbrella install/upgrade can run with crds.install: false.
helm upgrade lolday charts/lolday \
  --namespace lolday \
  --set kyverno.crds.install=false \
  ...
```

Audit-trail: `d93c6a8 fix(charts): disable kyverno crds.install in lolday umbrella [H-23-cluster]`.

### 2. `config.excludeKyvernoNamespace: true` (chart default) silently skips admission control

**Symptom:** Pods created in the `kyverno` namespace bypass the verifyImages
policy even when the policy `match.any.resources.namespaces` includes
`kyverno`. Or — more dangerously — Kyverno appears to enforce on
`lolday` / `lolday-jobs` but never logs a single admission event.

**Root cause.** The upstream Kyverno chart defaults `config.excludeKyvernoNamespace: true`.
This is **intended** to prevent Kyverno from blocking its own admission
controller restarts, but the chart-level option also installs an exclusion
that suppresses admission events from the `kyverno` namespace in the policy
report. With our verifyImages policy scoped to `[lolday, lolday-jobs]`
explicitly (D2), the chart-side exclusion adds a confusing second layer that
silently ate `kyverno`-ns admission events.

**Fix.** Override the chart default to `false`. Our policy already excludes
the `kyverno` namespace via `match.any.resources.namespaces`, so the
chart-side exclusion is redundant and only obscures the operator's view.

```yaml
# charts/lolday/values.yaml (kyverno sub-chart section)
kyverno:
  config:
    excludeKyvernoNamespace: false
```

Audit-trail: `60d911a fix(charts): disable kyverno excludeKyvernoNamespace [H-23-cluster]`.

### 3. The Kyverno init container references `:latest`

**Symptom:** Kyverno install succeeds but the `kyverno-init` Pod is stuck in
`ImagePullBackOff` if the registry refuses unspecified-tag pulls (we do, via
P4 cluster policy + Cosign), OR — silently worse — a future tag drift pulls
an unsigned `:latest`.

**Root cause.** The upstream chart hardcodes
`image: ghcr.io/kyverno/kyverno-init:latest` for one transient init job that
runs at install/upgrade time. The chart's main `image.tag` value does NOT
propagate to the init container.

**Fix.** Explicitly pin the init image to a versioned tag via chart values.
The version should match the main Kyverno version your sub-chart vendors.

```yaml
# charts/lolday/values.yaml (kyverno sub-chart section)
kyverno:
  initContainer:
    image:
      tag: v3.x.y # match kyverno.image.tag exactly
```

Audit-trail: `c073373 fix: P4 follow-ups — build-helpers cross-ns Secret, harbor_get_digest regex, kyverno :latest [M-harbor-sha-validate H-21-img H-23-cluster]`.

## Upgrade procedure

Follow these steps in order:

1. **Read the Kyverno release notes** for the version range you're crossing.
   CRD schema changes are mandatory-read because they imply step 2.
2. **Re-apply CRDs out-of-band** (gotcha #1 above) for the target version.
3. **Bump the sub-chart `tgz`** in `charts/lolday/charts/` via
   `helm dependency update`.
4. **Re-confirm the three overrides** are still in `values.yaml`:
   - `kyverno.crds.install: false`
   - `kyverno.config.excludeKyvernoNamespace: false`
   - `kyverno.initContainer.image.tag` pinned to the new version
5. **`helm upgrade lolday charts/lolday ...`** with the canonical 9-key
   `--set` chain (see `.claude/rules/charts-and-helm.md` and any phase plan
   pre-flight section).
6. **Verify ClusterPolicy still ready:**
   ```bash
   kubectl get clusterpolicy verify-lolday-image-signatures pss-baseline-audit-lolday \
     -o jsonpath='{range .items[*]}{.metadata.name}={.status.ready}{"\n"}{end}'
   ```
   Both must report `true`.
7. **Spot-check a non-signed image is rejected:**
   ```bash
   kubectl run test-unsigned --image=alpine:latest --restart=Never \
     --namespace=lolday --dry-run=server -o yaml
   ```
   Should produce a Kyverno admission error referencing the verifyImages
   policy. (The `--dry-run=server` ensures admission is evaluated but no Pod
   is created.)
8. **Tail kyverno logs for 30 seconds** to confirm policy evaluation events
   are flowing:
   ```bash
   kubectl -n kyverno logs -l app.kubernetes.io/component=admission-controller --tail=20 -f
   ```

## When to act

- **Quarterly:** check the upstream Kyverno release notes for the version
  family the sub-chart vendors. Bumps follow the upgrade procedure above.
- **On Dependabot alert:** Dependabot covers the `kyverno-init` image (P4
  H-21-img digest pinning) but NOT the chart `.tgz`. CHANGELOG-driven
  manual bumps remain operator-owned.
- **On audit failure:** if step 7 of the upgrade procedure shows a
  non-signed image getting through, Kyverno is mis-configured. Roll back via
  `helm rollback lolday <previous-rev>` and re-check gotchas #1-#3.

## Related artifacts

- Spec: [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../superpowers/specs/2026-05-12-security-hardening-design.md) §6.4
- Plan: [`docs/superpowers/plans/2026-05-12-security-hardening-p4-supply-chain.md`](../superpowers/plans/2026-05-12-security-hardening-p4-supply-chain.md)
- Charts: `charts/lolday/values.yaml` (kyverno block), `charts/lolday/charts/kyverno-*.tgz`
- P4 PR (squash-merged into main): [#139](https://github.com/bolin8017/lolday/pull/139)
- Six follow-up commits visible in P5/P6 preflight git log:
  `d93c6a8`, `cb59c46`, `60d911a`, `c073373`, `c44e27d`, `2a92939`
