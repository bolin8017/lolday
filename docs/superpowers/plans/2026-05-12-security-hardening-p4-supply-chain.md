# Security Hardening P4 — Supply Chain Pin & Verify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every byte that runs in the cluster traceable to a signed, scanned, immutable artifact — every image reference is content-addressed by digest, every CI build is Trivy-scanned + SBOM-attested + cosign-signed, and the cluster admits only images whose signatures match the lolday GHA workflow identity.

**Architecture:** Sixteen tasks across four chains. The **pinning chain (T1–T5)** is the foundation — every `image:` in `charts/lolday/values.yaml` and every `FROM` in every Dockerfile gets a `@sha256:<digest>` suffix; `helpers.lock` grows the same field; `scripts/build-helpers.sh` captures the digest after push; `scripts/check-image-tags-aligned.sh` extends to assert the digest layer is present; Dependabot's `docker` ecosystem coverage is reviewed against the new file inventory. The **CI signing chain (T6–T8)** appends Trivy-scan, SBOM-attest, and cosign-sign steps to the existing `docker-meta-build` composite — keyless OIDC, no static keys, Rekor as the public transparency log. The **cluster verification chain (T9–T10)** introduces Kyverno as a `charts/lolday` sub-chart with a `ClusterPolicy` that admission-time `verifyImages` against the GHCR-origin cosign signature; the policy is scoped to `lolday` + `lolday-jobs` namespaces to avoid the Kyverno self-bootstrap deadlock; PSS background audit is folded in alongside the K8s built-in Pod Security admission labels (P2). The **quality chain (T11–T16)** covers helper-image dependency hashes, the pytorch base-image bootstrap fix, the codecov fork-PR gate, a weekly Trivy cron for Dependabot-excluded base images, a SHA-format guard in `harbor_has_tag`, and the mlflow-server non-root run.

**Tech Stack:** Cosign 2.x (Sigstore), Sigstore Rekor public transparency log, GitHub Actions OIDC (`token.actions.githubusercontent.com`), Trivy (`aquasecurity/trivy-action`), Anchore SBOM (`anchore/sbom-action`, SPDX), Kyverno 3.x (CNCF Incubating), Helm 3 sub-chart pattern, `pip install --require-hashes`, `uv pip compile --generate-hashes`, `docker buildx imagetools inspect` for digest capture.

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.4.

**Finding IDs covered:** H-21-img (split across T1/T2/T3/T4/T5 for execution clarity), H-22, H-23, H-23-cluster (split across T9/T10), M-cache-poison, M-helper-hashes, M-pytorch-bootstrap, M-codecov-gate, M-trivy-cron, M-harbor-sha-validate, L-mlflow-user (11 spec findings; H-21-img split 5-way + H-23-cluster split 2-way = 16 implementation tasks).

---

## Design decisions (resolved up-front)

The implementer should not re-litigate these; they are locked.

**D1 — GHCR-only signing.** Cosign keyless sign runs in GHA against `ghcr.io/bolin8017/lolday-*` only. Harbor (`harbor.lolday.svc:80/lolday/*`) images are pushed manually by the operator from server30 via `scripts/build-helpers.sh` and `docker push` — there is no GHA OIDC available in that path, and a self-hosted runner is forbidden by `.claude/rules/github-actions.md`. Harbor-origin trust is covered by (1) Harbor's built-in Trivy scan on every push, (2) `M-harbor-sha-validate` (T15) regex-gating the SHA tag at upload, and (3) operator-only push discipline. Kyverno's `verifyImages` policy (T10) therefore matches **only the GHCR registry pattern**; Harbor refs are intentionally out of scope and pass through admission without signature check. Filing Harbor-side signing is a follow-up phase, not P4.

**D2 — Kyverno bootstrap order.** Kyverno installs as a Helm sub-chart of `charts/lolday`, alongside `harbor` / `kps` / `loki` / `alloy` / `trivy-operator` / `volcano` / `minio`. The verifyImages policy scopes `match.any.resources.namespaces: [lolday, lolday-jobs]` — Kyverno's own controllers in the `kyverno` namespace are excluded, so a subsequent `helm upgrade` that rolls Kyverno cannot reject Kyverno's own image during the rolling restart. CRDs are installed by the Kyverno sub-chart's own `helm.sh/hook: pre-install,pre-upgrade` per upstream chart convention (no manual `helm-charts-deploy-crds.yaml` shim). Webhook `failurePolicy: Fail` (mainstream default) — if Kyverno is down, lolday/lolday-jobs admissions block, which mirrors prod-grade installs.

**D3 — Cosign keyless trust root.** Issuer is `https://token.actions.githubusercontent.com` (GHA OIDC issuer, stable). Cert identity regex covers `main` push + semver tag push for both `images.yml` and `helpers.yml`:

```
^https://github\.com/bolin8017/lolday/\.github/workflows/(images|helpers)\.yml@refs/(heads/main|tags/v[0-9]+\.[0-9]+\.[0-9]+)$
```

This excludes PR runs (which never sign because `push: false`) and prevents identity-swap attacks from forks.

**D4 — Kyverno over Sigstore policy-controller.** Kyverno is CNCF Incubating with broad ecosystem (mutate/validate/generate/verifyImages) and folds the P2 PSS enforcement into background audit scans; policy-controller is Sigstore-native but narrower. Lolday's existing Helm sub-chart pattern (`harbor`, `kps`, `volcano`, `minio`, `trivy-operator`) makes Kyverno's sub-chart shape the natural fit. Decision: **Kyverno**.

**D5 — `check-image-tags-aligned.sh` extension.** The existing hook enforces tag-suffix alignment between `Chart.yaml.version` and `values.yaml` `lolday-backend` / `lolday-frontend` image tags. T1 extends the same hook with a second pass that asserts **every** `image:` in `values.yaml` ends in `@sha256:[0-9a-f]{64}` (excluding sub-chart templated stubs). Reusing the existing hook keeps `files: ^charts/lolday/(values\.yaml|Chart\.yaml)$` unchanged — no new pre-commit entry, one source of truth.

---

## Pre-flight

- [ ] **Confirm clean working tree on `main`.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git status
  git rev-parse HEAD
  ```

  Expected: working tree clean (modulo `backend/kube-prometheus-stack/` untracked, which is unrelated). HEAD at `adec4c2` (post-P3 ship) or newer.

- [ ] **Confirm helm rev 166 is the deployed release with chart v0.21.3.**

  ```bash
  helm -n lolday list | grep lolday
  ```

  Expected: `REVISION 166`, `STATUS deployed`, `CHART lolday-0.21.3`, `APP VERSION 0.21.3`.

- [ ] **Confirm backend pod runs v0.21.3 with `harbor_rotate` importable.**

  ```bash
  kubectl -n lolday exec deploy/backend -- /app/.venv/bin/python -c "from app.reconciler.harbor_rotate import reconcile_harbor_robot; print('ok:', reconcile_harbor_robot.__name__)"
  ```

  Expected: `ok: reconcile_harbor_robot`.

- [ ] **Confirm backend test baseline.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green. Capture the passed-count for the post-phase delta.

- [ ] **Confirm `helm lint` baseline (post-P3 `backend.fernetKeys` flag).**

  Cache the command — every helm-lint step in this plan uses the same set, with extra `--set kyverno.enabled=true` added from T9 onward:

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test \
    --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test \
    --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. The Missing required value INFO line for `backend.harborAdminPassword` is the chart's required() validator surfacing — not a lint failure.

- [ ] **Confirm `pre-commit` baseline.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: all hooks green. If anything fails, fix the root cause before starting P4. **Do NOT use `--no-verify`** (per project hard rule).

- [ ] **Create the feature branch.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git checkout -b security-hardening-p4
  ```

  The plan itself is committed directly to `main` (continuation of the security spec). All P4 task commits land on `security-hardening-p4` and squash-merge back to `main` via a single PR per the P1/P2/P3 pattern.

---

## Task 1: [H-21-img a] Pin lolday-own image digests in `values.yaml` + extend the alignment hook

**Findings:** H-21-img (HIGH, lolday-own image refs slice). Recommended model: **opus** (touches three release-flow surfaces — values.yaml, hook script, hook test).

**Files:**

- Modify: `charts/lolday/values.yaml` (lines 36, 258, 297)
- Modify: `scripts/check-image-tags-aligned.sh`
- Test: `scripts/test_check_image_tags_aligned.bats` (new BATS suite, or extend the existing test harness if one exists)

**Rationale:** Today `backend.image`, `frontend.image`, `mlflow.image` end at the tag (`:v0.21.3` / `:v2.20.3-boto3`). Harbor tags are immutable per its own retention policy, but a registry compromise (Harbor restore from a tampered backup, MITM on the in-cluster push) could republish the tag with malicious content; nothing in the chart proves the bytes pulled at deploy match the bytes pushed at release. Pinning `@sha256:<digest>` makes the reference content-addressable end-to-end. The digest is captured at release time via `docker buildx imagetools inspect` after the operator pushes; the existing `image-tags-aligned` hook gets a second assertion pass that fails any `image:` line in `values.yaml` missing the `@sha256:` suffix.

- [ ] **Step 1: Capture the current digests for backend / frontend / mlflow images.**

  Run on server30 (where the Harbor registry is reachable):

  ```bash
  BACKEND_DIGEST=$(docker buildx imagetools inspect harbor.lolday.svc:80/lolday/lolday-backend:v0.21.3 --format '{{.Manifest.Digest}}')
  FRONTEND_DIGEST=$(docker buildx imagetools inspect harbor.lolday.svc:80/lolday/lolday-frontend:v0.21.3 --format '{{.Manifest.Digest}}')
  MLFLOW_DIGEST=$(docker buildx imagetools inspect harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3 --format '{{.Manifest.Digest}}')
  echo "backend  : $BACKEND_DIGEST"
  echo "frontend : $FRONTEND_DIGEST"
  echo "mlflow   : $MLFLOW_DIGEST"
  ```

  Expected: three `sha256:...` lines, 64 hex chars after the prefix. Save them — they get pasted into Step 2.

- [ ] **Step 2: Modify `charts/lolday/values.yaml`.**

  At line 36, replace:

  ```yaml
  image: harbor.lolday.svc:80/lolday/lolday-backend:v0.21.3
  ```

  with (substituting the captured digest):

  ```yaml
  # H-21-img: digest pin makes the reference content-addressable. The
  # tag suffix stays because Harbor's display + check-image-tags-aligned
  # hook both key off the version. Capture the digest via
  #   docker buildx imagetools inspect harbor.lolday.svc:80/lolday/lolday-backend:v0.21.3 --format '{{.Manifest.Digest}}'
  # at release time.
  image: harbor.lolday.svc:80/lolday/lolday-backend:v0.21.3@sha256:<BACKEND_DIGEST_BARE>
  ```

  Where `<BACKEND_DIGEST_BARE>` is the 64 hex chars (omit the `sha256:` prefix that `docker buildx imagetools inspect` prints; the `@sha256:` is already in the literal).

  At line 297, the same change for frontend:

  ```yaml
  image: harbor.lolday.svc:80/lolday/lolday-frontend:v0.21.3@sha256:<FRONTEND_DIGEST_BARE>
  ```

  At line 258, the same change for mlflow (note its tag is `v2.20.3-boto3`, NOT `v0.21.3` — `mlflow-server` is a manually-pinned helper, not a lolday release-tagged image):

  ```yaml
  image: harbor.lolday.svc:80/lolday/mlflow-server:v2.20.3-boto3@sha256:<MLFLOW_DIGEST_BARE>
  ```

- [ ] **Step 3: Extend `scripts/check-image-tags-aligned.sh`.**

  Replace the existing file's body (preserving the shebang, the `set -euo pipefail`, and the existing tag-alignment pass) with:

  ```bash
  #!/usr/bin/env bash
  # Two assertions:
  #   1. Chart.yaml version + appVersion + lolday-backend / lolday-frontend
  #      image tags are all aligned (catches half-bumped release).
  #   2. Every `image:` line in values.yaml ends in @sha256:<64 hex>
  #      (H-21-img: digest pin is mandatory for content-addressable refs).
  #
  # Usage:
  #   bash scripts/check-image-tags-aligned.sh
  #
  # Exit codes:
  #   0 — both assertions pass
  #   1 — divergence detected; remediation printed to stderr

  set -euo pipefail

  REPO_ROOT="${LOLDAY_REPO_ROOT_OVERRIDE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
  CHART_DIR="$REPO_ROOT/charts/lolday"
  VALUES="$CHART_DIR/values.yaml"
  CHART="$CHART_DIR/Chart.yaml"

  # ---- Pass 1: tag alignment (existing behavior, unchanged) ----
  chart_version=$(awk '/^version:/ {print $2; exit}' "$CHART" | tr -d '"')
  chart_appversion=$(awk '/^appVersion:/ {print $2; exit}' "$CHART" | tr -d '"')

  # Match `image: harbor.lolday.svc:80/lolday/lolday-{backend,frontend}:vX.Y.Z[@sha256:...]`
  backend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-backend:" "$VALUES" \
    | head -1 | sed -E 's|.*lolday-backend:([^@[:space:]#]+).*|\1|')
  frontend_tag=$(grep -E "^[[:space:]]*image:[[:space:]]+harbor\.lolday\.svc:80/lolday/lolday-frontend:" "$VALUES" \
    | head -1 | sed -E 's|.*lolday-frontend:([^@[:space:]#]+).*|\1|')

  if [ -z "$chart_version" ] || [ -z "$chart_appversion" ] || [ -z "$backend_tag" ] || [ -z "$frontend_tag" ]; then
    {
      echo "ERROR: could not parse one of the four expected fields:"
      echo "  Chart.yaml version    = '$chart_version'"
      echo "  Chart.yaml appVersion = '$chart_appversion'"
      echo "  values.yaml backend   = '$backend_tag'"
      echo "  values.yaml frontend  = '$frontend_tag'"
    } >&2
    exit 1
  fi

  expected="v$chart_version"
  fail=0

  if [ "$chart_appversion" != "$chart_version" ]; then
    echo "ERROR: Chart.yaml appVersion ($chart_appversion) != version ($chart_version)" >&2
    fail=1
  fi
  if [ "$backend_tag" != "$expected" ]; then
    echo "ERROR: backend image tag $backend_tag != expected $expected" >&2
    fail=1
  fi
  if [ "$frontend_tag" != "$expected" ]; then
    echo "ERROR: frontend image tag $frontend_tag != expected $expected" >&2
    fail=1
  fi

  # ---- Pass 2: digest pin (NEW for H-21-img) ----
  # Every `image:` scalar line in values.yaml must end in @sha256:<64-hex>.
  # Excludes pure-tag sub-fields under chart values (`tag: v2.15.0`) — those
  # are handled by T4. The grep regex restricts to a literal image-ref line.
  while IFS= read -r line; do
    ref=$(echo "$line" | sed -E 's|^[[:space:]]*image:[[:space:]]+([^[:space:]#]+).*|\1|')
    if ! echo "$ref" | grep -qE '@sha256:[0-9a-f]{64}$'; then
      echo "ERROR: image ref missing @sha256:<64-hex> digest pin: $ref" >&2
      fail=1
    fi
  done < <(grep -E "^[[:space:]]*image:[[:space:]]+[a-zA-Z0-9./_:-]+(:[a-zA-Z0-9._-]+)?(@sha256:[0-9a-f]+)?" "$VALUES")

  if [ "$fail" -eq 1 ]; then
    cat >&2 <<'EOF'

  Release commits must:
    1. Bump Chart.yaml version + appVersion + values.yaml backend/frontend tags together.
    2. Pin every `image:` in values.yaml via @sha256:<digest>. Capture digests at release time:
         docker buildx imagetools inspect <ref> --format '{{.Manifest.Digest}}'

  See docs/runbooks/deploy.md §release flow and docs/superpowers/specs/2026-05-12-security-hardening-design.md H-21-img.
  EOF
    exit 1
  fi

  echo "image tags aligned with Chart.yaml: $expected; digest pin present on all values.yaml image refs"
  ```

- [ ] **Step 4: Run the hook against the modified values.yaml.**

  ```bash
  bash scripts/check-image-tags-aligned.sh
  ```

  Expected: `image tags aligned with Chart.yaml: v0.21.3; digest pin present on all values.yaml image refs`.

  If it fails with `image ref missing @sha256:`, recheck that every line modified in Step 2 has the digest suffix.

- [ ] **Step 5: Run the hook against an intentionally broken values.yaml to confirm failure.**

  ```bash
  cp charts/lolday/values.yaml /tmp/values.yaml.bak
  sed -i 's|@sha256:.*||' charts/lolday/values.yaml  # strip digests
  bash scripts/check-image-tags-aligned.sh && echo "BUG: hook accepted unpinned values.yaml"
  cp /tmp/values.yaml.bak charts/lolday/values.yaml
  rm /tmp/values.yaml.bak
  ```

  Expected: `ERROR: image ref missing @sha256:<64-hex> digest pin: ...` and a non-zero exit; final line `BUG:` does NOT print.

- [ ] **Step 6: helm-lint with the modified values.yaml.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 7: Commit.**

  ```bash
  git add charts/lolday/values.yaml scripts/check-image-tags-aligned.sh
  git commit -m "$(cat <<'EOF'
  feat(charts): digest-pin backend/frontend/mlflow images + hook check [H-21-img]

  Every `image:` scalar in values.yaml now ends in @sha256:<64-hex>. The
  reference becomes content-addressable end-to-end: a Harbor restore from
  a tampered backup or an in-cluster MITM that republishes the same tag
  cannot serve different bytes. check-image-tags-aligned.sh gains a
  second pass that fails any image: line missing the digest suffix —
  the hook already runs on values.yaml/Chart.yaml changes via pre-commit,
  so the digest layer is enforced at the same gate as the tag layer.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2: [H-21-img b] Pin helper image digests in `helpers.lock` + capture in `build-helpers.sh`

**Findings:** H-21-img (HIGH, helper image refs slice). Recommended model: **opus** (the build script and lock format change in lockstep).

**Files:**

- Modify: `scripts/build-helpers.sh` (`docker_build_push` + `write_lock` body)
- Modify: `charts/lolday/helpers.lock`
- Modify: `scripts/check-helpers-lock.sh`

**Rationale:** `helpers.lock` today pins a content-addressable tag (12-char subtree SHA) — the tag IS the unforgeable identifier inside the repo. But once that tag lands in Harbor, the docker-tag pointer can be moved (server-side, by anyone with `repository:lolday/build-helper:push` permission). Adding `@sha256:<image-digest>` makes the lock entry double-anchored: the subtree SHA proves the source matches; the image digest proves the Harbor manifest matches. The lock entry shape changes from `harbor.../build-helper:9f45d263d9d2` to `harbor.../build-helper:9f45d263d9d2@sha256:<digest>`. `deploy.sh` already passes the lock entry as `--set backend.env.BUILD_IMAGE_HELPER=$BUILD_IMAGE_HELPER` — the string concatenation is transparent to the K8s API, no chart change needed.

- [ ] **Step 1: Modify `scripts/build-helpers.sh::docker_build_push` to capture the digest after push.**

  Locate the function `docker_build_push` (around line 168). After the final `docker push "$ref"` line, add:

  ```bash
    # H-21-img: capture the manifest digest after push. Harbor v2 returns it
    # in the Docker-Content-Digest header, but `docker push` already prints it
    # to stdout via `<sha256:abc...> size: <bytes>` on a successful push. The
    # cleaner extraction path is `docker buildx imagetools inspect` which
    # round-trips and normalizes.
    local digest
    digest="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')"
    if ! echo "$digest" | grep -qE '^sha256:[0-9a-f]{64}$'; then
      echo "ERROR: unexpected digest format from buildx imagetools inspect: $digest" >&2
      return 1
    fi
    # Print as `<ref>@<digest>` so the caller can append to helpers.lock.
    echo "${ref}@${digest}"
  ```

  Then update the call sites that read `docker_build_push`'s output. The function previously printed nothing on success; the new contract is that on success it prints exactly one line to stdout: `<ref>@<digest>`. Locate the call site in the per-helper loop and capture the digest-bearing ref into a per-helper variable.

  Concretely, in the same script find the section that loops over `HELPERS` (likely near the bottom of the script) and replace the pattern:

  ```bash
  docker_build_push "$name" "$sha"
  ```

  with:

  ```bash
  pinned_ref="$(docker_build_push "$name" "$sha")"
  case "$name" in
    build-helper) BUILD_HELPER_PINNED="$pinned_ref" ;;
    job-helper)   JOB_HELPER_PINNED="$pinned_ref" ;;
  esac
  ```

- [ ] **Step 2: Modify the `write_lock` function in `scripts/build-helpers.sh`.**

  The lock writer today emits:

  ```json
  {
    "build_helper": "harbor.lolday.svc:80/lolday/build-helper:9f45d263d9d2",
    "job_helper": "harbor.lolday.svc:80/lolday/job-helper:11108da6a065"
  }
  ```

  Update it to consume the digest-bearing refs captured in Step 1:

  ```bash
  write_lock() {
    local lockfile="$1"
    : "${BUILD_HELPER_PINNED:?build-helper digest-pinned ref required (Step 1 capture failed?)}"
    : "${JOB_HELPER_PINNED:?job-helper digest-pinned ref required (Step 1 capture failed?)}"
    cat > "$lockfile" <<EOF
  {
    "build_helper": "${BUILD_HELPER_PINNED}",
    "job_helper": "${JOB_HELPER_PINNED}"
  }
  EOF
  }
  ```

  Result lock shape:

  ```json
  {
    "build_helper": "harbor.lolday.svc:80/lolday/build-helper:9f45d263d9d2@sha256:abc...",
    "job_helper": "harbor.lolday.svc:80/lolday/job-helper:11108da6a065@sha256:def..."
  }
  ```

- [ ] **Step 3: Tighten `scripts/check-helpers-lock.sh` to assert the new digest field.**

  Find the helper-lock parse near the top of `check-helpers-lock.sh`. After the existing tag-SHA assertion, add:

  ```bash
  # H-21-img: lock entries must include @sha256:<64-hex> after the
  # subtree-SHA tag. build-helpers.sh captures the digest post-push.
  for helper in build_helper job_helper; do
    ref="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$LOCK" "$helper")"
    if ! echo "$ref" | grep -qE '@sha256:[0-9a-f]{64}$'; then
      echo "ERROR: helpers.lock entry $helper missing @sha256:<64-hex> digest pin: $ref" >&2
      exit 1
    fi
  done
  ```

  Run a syntax check:

  ```bash
  bash -n scripts/check-helpers-lock.sh
  bash -n scripts/build-helpers.sh
  ```

  Expected: clean.

- [ ] **Step 4: Regenerate `helpers.lock` from a real push.**

  This step must be done on server30 where Harbor is reachable. From a clean tree:

  ```bash
  bash scripts/build-helpers.sh
  cat charts/lolday/helpers.lock
  ```

  Expected: both `build_helper` and `job_helper` entries end in `@sha256:<64-hex>`.

  The script's existing dirty-tree refusal still applies — if the working tree has uncommitted changes outside the helpers' subtrees, the build step refuses (this is the intentional guard from `.claude/rules/scripts-and-ops.md`).

- [ ] **Step 5: Run the helpers-lock pre-commit hook.**

  ```bash
  pre-commit run helpers-lock-fresh --all-files
  ```

  Expected: pass. The hook re-runs `check-helpers-lock.sh` which now asserts the digest.

- [ ] **Step 6: Run `deploy.sh` dry-run to confirm the new lock entry flows through.**

  ```bash
  # Capture the helm command without executing — diff against pre-change.
  bash -x scripts/deploy.sh 2>&1 | grep -E 'BUILD_IMAGE_HELPER|JOB_HELPER_IMAGE' | head -5
  ```

  Expected: the `--set backend.env.BUILD_IMAGE_HELPER=...@sha256:...` lines show the digest suffix.

- [ ] **Step 7: Commit.**

  ```bash
  git add scripts/build-helpers.sh scripts/check-helpers-lock.sh charts/lolday/helpers.lock
  git commit -m "$(cat <<'EOF'
  feat(scripts): digest-pin helpers.lock entries; build-helpers.sh captures digest [H-21-img]

  Lock entries shape changed from `<ref>:<sha-tag>` to `<ref>:<sha-tag>@sha256:<digest>`.
  build-helpers.sh now captures the manifest digest via `docker buildx
  imagetools inspect` after each successful push. check-helpers-lock.sh
  fails any entry missing the digest suffix. deploy.sh wires the full
  ref string through `--set backend.env.{BUILD_IMAGE_HELPER,JOB_HELPER_IMAGE}=`
  unchanged — the change is transparent at the chart layer.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3: [H-21-img c] Pin Dockerfile `FROM` digests across every Dockerfile

**Findings:** H-21-img (HIGH, Dockerfile FROM slice). Recommended model: **sonnet** (mechanical edits across 6 Dockerfiles).

**Files:**

- Modify: `backend/Dockerfile:1`
- Modify: `frontend/Dockerfile:2`, `:18`
- Modify: `charts/lolday/helpers/build-helper/Dockerfile:1`
- Modify: `charts/lolday/helpers/job-helper/Dockerfile:1`
- Modify: `charts/lolday/helpers/mlflow-server/Dockerfile:1`
- Modify: `charts/lolday/helpers/pytorch-cu12-base/Dockerfile:18`

**Rationale:** Every Dockerfile `FROM` line today pins a tag (e.g., `FROM python:3.14-slim`, `FROM node:22-alpine`, `FROM nginxinc/nginx-unprivileged:1.29-alpine`). Tag pointers are mutable upstream — Docker Hub republishes the same tag whenever the maintainer rebuilds the base. The result: two consecutive `docker build`s of the same Dockerfile can produce different layers. Pinning `FROM <image>:<tag>@sha256:<digest>` guarantees byte-identical base layers across rebuilds; Dependabot auto-PRs the digest forward when upstream publishes a new tag. The backend Dockerfile's `COPY --from=ghcr.io/astral-sh/uv:0.11.13@sha256:...` line already follows the pattern; this task extends it to every other `FROM`.

- [ ] **Step 1: Capture the current upstream digests.**

  Run anywhere with internet access (the operator workstation is fine):

  ```bash
  for ref in python:3.14-slim python:3.12-slim node:22-alpine nginxinc/nginx-unprivileged:1.29-alpine ghcr.io/mlflow/mlflow:v2.20.3 nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04; do
    echo -n "$ref -> "
    docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}'
  done
  ```

  Expected: six `sha256:...` lines. Save them.

- [ ] **Step 2: Modify `backend/Dockerfile:1`.**

  Replace:

  ```dockerfile
  FROM python:3.14-slim AS base
  ```

  with (substituting the captured digest):

  ```dockerfile
  # H-21-img: base image pinned by digest. Dependabot's `docker` ecosystem
  # tracks /backend and auto-PRs digest bumps as upstream republishes the tag.
  FROM python:3.14-slim@sha256:<PYTHON_314_DIGEST> AS base
  ```

- [ ] **Step 3: Modify `frontend/Dockerfile:2` and `:18`.**

  Line 2:

  ```dockerfile
  FROM node:22-alpine@sha256:<NODE_22_DIGEST> AS build
  ```

  Line 18:

  ```dockerfile
  FROM nginxinc/nginx-unprivileged:1.29-alpine@sha256:<NGINX_UNPRIV_DIGEST>
  ```

- [ ] **Step 4: Modify the four helper Dockerfiles.**

  `charts/lolday/helpers/build-helper/Dockerfile:1`:

  ```dockerfile
  FROM python:3.12-slim@sha256:<PYTHON_312_DIGEST>
  ```

  `charts/lolday/helpers/job-helper/Dockerfile:1`:

  ```dockerfile
  FROM python:3.12-slim@sha256:<PYTHON_312_DIGEST>
  ```

  `charts/lolday/helpers/mlflow-server/Dockerfile:1`:

  ```dockerfile
  # H-21-img: upstream MLflow base pinned by digest. Bumping the tag
  # (e.g., v2.20.3 -> v2.21.0) is a manual operator action (per
  # .github/dependabot.yml comment) — Dependabot does not auto-PR this
  # file. The digest pin makes any retag silently re-anchor visible.
  FROM ghcr.io/mlflow/mlflow:v2.20.3@sha256:<MLFLOW_BASE_DIGEST>
  ```

  `charts/lolday/helpers/pytorch-cu12-base/Dockerfile:18`:

  ```dockerfile
  FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04@sha256:<NVIDIA_CUDA_DIGEST>
  ```

- [ ] **Step 5: Build each Dockerfile to confirm no regression.**

  On a host with docker buildx:

  ```bash
  docker build -t test-backend backend/
  docker build -t test-frontend frontend/
  docker build -t test-build-helper charts/lolday/helpers/build-helper/
  docker build -t test-job-helper charts/lolday/helpers/job-helper/
  docker build -t test-mlflow charts/lolday/helpers/mlflow-server/
  docker build -t test-pytorch charts/lolday/helpers/pytorch-cu12-base/
  ```

  Expected: each build succeeds. The two manually-pinned helpers (mlflow-server, pytorch-cu12-base) are slow (~10–25 min) — running them is OPTIONAL during the PR cycle but MUST happen before the operator promotes the PR. Document this in the PR body.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/Dockerfile frontend/Dockerfile charts/lolday/helpers/*/Dockerfile
  git commit -m "$(cat <<'EOF'
  feat(docker): pin every FROM by @sha256:<digest> [H-21-img]

  Every Dockerfile FROM now ends in @sha256:<64-hex>. Upstream tag
  republishes (the rolling layer-refresh pattern that quietly shifts
  base layers between consecutive `docker build`s) become explicit:
  Dependabot's docker ecosystem auto-PRs digest bumps on the four
  Dependabot-tracked dirs (backend/, frontend/, helpers/build-helper,
  helpers/job-helper). mlflow-server and pytorch-cu12-base stay
  excluded from Dependabot per the existing rationale; digest moves
  there are operator-driven.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4: [H-21-img d] Pin sub-chart image digests for postgres / redis / cloudflared / postgres-exporter / loki sidecar / harbor stack

**Findings:** H-21-img (HIGH, sub-chart image slice). Recommended model: **opus** (chart values plumbing varies across sub-charts).

**Files:**

- Modify: `charts/lolday/values.yaml` (multiple sub-chart image blocks)

**Rationale:** Sub-charts (`postgresql.image`, `redis.image`, `cloudflare.image`, `monitoring.postgresExporter.image`, `loki.sidecar.image.tag`, every `harbor.*.image.tag`) carry the same risk as the lolday-own images — upstream republish under the same tag silently shifts the layer set. Each sub-chart's values schema varies: some accept `image:` as a full ref string (postgresql, redis, cloudflare, postgres-exporter), some use a nested `image.repository + image.tag` (harbor sub-chart, loki sidecar). For nested-schema charts, Helm renders the final image string as `{{.repository}}:{{.tag}}`, so embedding the digest into `tag` (`tag: "v2.15.0@sha256:..."`) works for charts whose template doesn't validate the `tag` as semver. Each row below names the correct schema slot for that sub-chart.

- [ ] **Step 1: Capture sub-chart image digests.**

  ```bash
  for ref in \
    postgres:16-alpine \
    redis:7.4-alpine \
    cloudflare/cloudflared:2026.3.0 \
    quay.io/prometheuscommunity/postgres-exporter:v0.17.0 \
    kiwigrid/k8s-sidecar:2.7.1 \
    goharbor/nginx-photon:v2.15.0 \
    goharbor/harbor-portal:v2.15.0 \
    goharbor/harbor-core:v2.15.0 \
    goharbor/harbor-jobservice:v2.15.0 \
    goharbor/registry-photon:v2.15.0 \
    goharbor/harbor-registryctl:v2.15.0 \
    goharbor/harbor-db:v2.15.0 \
    goharbor/redis-photon:v2.15.0 \
    goharbor/harbor-exporter:v2.15.0 \
    goharbor/trivy-adapter-photon:v2.15.0; do
    echo -n "$ref -> "
    docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}'
  done
  ```

  Expected: 15 `sha256:...` lines. Save them in a scratch file — they get pasted into Step 2/3/4.

- [ ] **Step 2: Update full-ref sub-chart blocks in `values.yaml`.**

  Three sub-charts use a single `image:` scalar. Update each with `:tag@sha256:<digest>`:
  - Line 29 (cloudflared):
    ```yaml
    image: cloudflare/cloudflared:2026.3.0@sha256:<CLOUDFLARED_DIGEST>
    ```
  - Line 118 (postgresql):
    ```yaml
    image: postgres:16-alpine@sha256:<POSTGRES_DIGEST>
    ```
  - Line 134 (redis):
    ```yaml
    image: redis:7.4-alpine@sha256:<REDIS_DIGEST>
    ```
  - Line 314 (postgres-exporter):
    ```yaml
    image: quay.io/prometheuscommunity/postgres-exporter:v0.17.0@sha256:<PG_EXPORTER_DIGEST>
    ```

- [ ] **Step 3: Update the Harbor sub-chart's nested `image.tag` slots.**

  The Harbor chart (1.18.3) renders image refs as `{{.repository}}:{{.tag}}`. Embedding the digest in `tag` is the supported way; the upstream chart does not separately accept `image.digest`. Update each of the 10 harbor `image.tag` slots between lines 152 and 215:

  ```yaml
  harbor:
    nginx:
      image:
        tag: v2.15.0@sha256:<HARBOR_NGINX_DIGEST>
    portal:
      image:
        tag: v2.15.0@sha256:<HARBOR_PORTAL_DIGEST>
    core:
      image:
        tag: v2.15.0@sha256:<HARBOR_CORE_DIGEST>
    jobservice:
      image:
        tag: v2.15.0@sha256:<HARBOR_JOBSERVICE_DIGEST>
    registry:
      registry:
        image:
          tag: v2.15.0@sha256:<HARBOR_REGISTRY_DIGEST>
      controller:
        image:
          tag: v2.15.0@sha256:<HARBOR_REGISTRYCTL_DIGEST>
    database:
      internal:
        image:
          tag: v2.15.0@sha256:<HARBOR_DB_DIGEST>
    redis:
      internal:
        image:
          tag: v2.15.0@sha256:<HARBOR_REDIS_DIGEST>
    exporter:
      image:
        tag: v2.15.0@sha256:<HARBOR_EXPORTER_DIGEST>
    trivy:
      enabled: true
      skipUpdate: false
      image:
        tag: v2.15.0@sha256:<HARBOR_TRIVY_DIGEST>
  ```

- [ ] **Step 4: Update the loki sidecar image tag (line 514).**

  ```yaml
  sidecar:
    image:
      tag: 2.7.1@sha256:<LOKI_SIDECAR_DIGEST>
  ```

- [ ] **Step 5: helm-render and grep for any remaining unpinned ref.**

  ```bash
  helm dependency update charts/lolday >/dev/null
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | grep -E '^\s+image:' | grep -vE '@sha256:[0-9a-f]+' | head -20
  ```

  Expected: empty output (every rendered `image:` line carries `@sha256:`). If a line still leaks unpinned, the most common cause is a sub-chart whose template renders `image: "{{ .Values.image.repository }}/foo:{{ .Values.image.tag }}"` but the values schema doesn't expose `tag` at the level we set it — re-grep with the chart's actual rendered key path and adjust.

  **Known-leaks (acceptable, document in PR body):** images created on the fly by sub-charts (e.g., Helm hook Jobs that pull `bitnami/kubectl`) may not flow through values overrides. Trivy-cron (T14) is the safety net for those.

- [ ] **Step 6: helm-lint.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `0 chart(s) failed`.

- [ ] **Step 7: Commit.**

  ```bash
  git add charts/lolday/values.yaml
  git commit -m "$(cat <<'EOF'
  feat(charts): digest-pin sub-chart image tags (postgres/redis/harbor/...) [H-21-img]

  Every sub-chart image rendered into a Pod spec now carries a
  @sha256:<digest> suffix. Harbor sub-chart (10 images), loki sidecar,
  postgres / redis / cloudflared / postgres-exporter all use the
  `tag: vX.Y.Z@sha256:abc...` form, which the upstream chart templates
  accept transparently because they render `{{.repository}}:{{.tag}}`.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5: [H-21-img e] Confirm Dependabot coverage post-pinning

**Findings:** H-21-img (HIGH, Dependabot slice). Recommended model: **sonnet** (small config + comment refresh).

**Files:**

- Modify: `.github/dependabot.yml`

**Rationale:** `.github/dependabot.yml` already declares the `docker` ecosystem on four directories: `/backend`, `/frontend`, `/charts/lolday/helpers/build-helper`, `/charts/lolday/helpers/job-helper`. Once T3 pins every `FROM` by digest, Dependabot's docker ecosystem auto-PRs **both** tag bumps (e.g., `python:3.14-slim` → `python:3.15-slim`) and digest-only re-anchors (same tag, new manifest). `mlflow-server` and `pytorch-cu12-base` stay excluded per the existing rationale (operator-coordinated tag bumps), with the new digest-pin layer covered by M-trivy-cron (T14) for security-driven re-anchor visibility. This task is mostly a comment refresh + an explicit confirmation step — the directory list is already correct.

- [ ] **Step 1: Update the Dependabot config comment.**

  In `.github/dependabot.yml`, locate the comment block (lines 66–78) and replace it with:

  ```yaml
  # mlflow-server and pytorch-cu12-base are intentionally NOT tracked by
  # dependabot. Both are manually pinned helper images per
  # .claude/rules/charts-and-helm.md ("Manually pinned (semantic tags)"):
  #   - mlflow-server tag carries the upstream MLflow version (e.g. v2.20.3)
  #     and bumping it requires a coordinated mlflow-skinny client upgrade
  #     in backend/pyproject.toml + proxy-test refresh.
  #   - pytorch-cu12-base is locked to CUDA 12.6 by host driver 560 on
  #     server30; the directory name itself encodes the constraint and the
  #     Dockerfile pins torch wheels to the cu126 index.
  # .github/workflows/helpers.yml already excludes both from CI build
  # verification. After H-21-img (P4 plan T3), both Dockerfiles also pin
  # their base image by @sha256:<digest>. Digest-only re-anchors of the
  # excluded helpers are surfaced by .github/workflows/trivy-cron.yml
  # (P4 plan T14) on a weekly cadence — when Trivy reports CRITICAL on
  # the current digest, the operator updates the Dockerfile FROM line
  # manually.
  ```

- [ ] **Step 2: Validate the YAML.**

  ```bash
  python3 -c 'import yaml; yaml.safe_load(open(".github/dependabot.yml"))' && echo "YAML OK"
  ```

  Expected: `YAML OK`.

- [ ] **Step 3: Confirm the existing entry list matches the post-T3 file inventory.**

  ```bash
  # Each entry under updates: with package-ecosystem: docker should point at a
  # directory containing a Dockerfile.
  yq '.updates[] | select(.["package-ecosystem"] == "docker") | .directory' .github/dependabot.yml | while read dir; do
    dir="${dir%\"}"; dir="${dir#\"}"
    [ -f ".${dir}/Dockerfile" ] && echo "OK: .${dir}/Dockerfile" || echo "MISSING: .${dir}/Dockerfile"
  done
  ```

  Expected: four `OK:` lines (backend, frontend, build-helper, job-helper); zero `MISSING:` lines.

- [ ] **Step 4: Commit.**

  ```bash
  git add .github/dependabot.yml
  git commit -m "$(cat <<'EOF'
  chore(dependabot): refresh comment for post-H-21-img digest tracking [H-21-img]

  Existing `package-ecosystem: docker` entries (backend, frontend,
  build-helper, job-helper) already cover the four Dependabot-tracked
  Dockerfiles. With every FROM digest-pinned (P4 plan T3), Dependabot
  auto-PRs both tag bumps and digest re-anchors on those four. The
  excluded helpers (mlflow-server, pytorch-cu12-base) gain trivy-cron
  coverage instead (P4 plan T14). Comment updated to reflect the new
  layering.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6: [M-cache-poison] Scope buildx GHA cache per-image and per-ref

**Findings:** M-cache-poison (MEDIUM). Recommended model: **sonnet** (two-line edit in one composite).

**Files:**

- Modify: `.github/actions/docker-meta-build/action.yml:45-46`

**Rationale:** Today `cache-from: type=gha,scope=${{ inputs.image }}` and `cache-to: type=gha,scope=${{ inputs.image }},mode=max` — every workflow run for the same image name shares the same cache namespace. A PR that builds a malicious layer can poison the cache and have that layer reused by the next `main` push (cache poisoning). Tightening the scope to `${{ inputs.image }}-${{ github.ref_name }}` segregates per-ref caches — `main`'s cache cannot be poisoned by `PR-1234`'s build. Cache hit rate drops slightly for cross-branch builds; mainstream tradeoff favours integrity.

- [ ] **Step 1: Modify the two scope lines.**

  In `.github/actions/docker-meta-build/action.yml`, change lines 45–46 from:

  ```yaml
  cache-from: type=gha,scope=${{ inputs.image }}
  cache-to: type=gha,scope=${{ inputs.image }},mode=max
  ```

  to:

  ```yaml
  # M-cache-poison: per-image + per-ref scope. A PR that poisons its
  # own cache namespace cannot bleed into main's cache. Cache hit rate
  # drops marginally for cross-ref builds; integrity is the priority.
  cache-from: type=gha,scope=${{ inputs.image }}-${{ github.ref_name }}
  cache-to: type=gha,scope=${{ inputs.image }}-${{ github.ref_name }},mode=max
  ```

- [ ] **Step 2: actionlint the composite (best-effort).**

  Without an actionlint pre-commit hook (forbidden per `.claude/rules/github-actions.md`), use GHA's own parser by triggering a workflow_dispatch on a feature branch later — for this step, validate the YAML shape:

  ```bash
  python3 -c 'import yaml; yaml.safe_load(open(".github/actions/docker-meta-build/action.yml"))' && echo "YAML OK"
  ```

  Expected: `YAML OK`.

- [ ] **Step 3: Commit.**

  ```bash
  git add .github/actions/docker-meta-build/action.yml
  git commit -m "$(cat <<'EOF'
  fix(ci): scope buildx GHA cache per image + per ref [M-cache-poison]

  Previously `scope=${{ inputs.image }}` — all workflow runs for the
  same image shared a single cache namespace, so a PR build could
  poison the cache reused by the next main push. Tighten to
  `${{ inputs.image }}-${{ github.ref_name }}`: PR-N's cache cannot
  contaminate main's, and vice versa. Marginal cache-hit cost across
  refs; integrity is the priority.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7: [H-22] Append Trivy scan + SBOM attestation to `docker-meta-build`

**Findings:** H-22 (HIGH). Recommended model: **opus** (composite must remain idempotent; failure-policy decisions are load-bearing).

**Files:**

- Modify: `.github/actions/docker-meta-build/action.yml`

**Rationale:** Today the composite builds + pushes the image. After H-22, every push (main + tag) is followed by (a) a Trivy filesystem-mode scan that fails the build on `CRITICAL` and (b) an Anchore SBOM action that attaches an SPDX SBOM to the image. PRs build but don't push, so the scan still runs (against the locally built image), giving fast CRITICAL feedback before merge. Trivy's `severity: CRITICAL` + `exit-code: 1` is mainstream — Trivy treats unknown severities as non-blocking, so HIGH / MEDIUM / LOW still surface in the action's stdout for review but don't fail the pipeline. SBOM attachment uses Cosign-compatible attestation so a later `cosign download attestation` works against any pushed digest.

- [ ] **Step 1: Insert the Trivy + SBOM steps into the composite.**

  After the `Build and push` step (line 38–46), append (preserving the existing steps above):

  ```yaml
  # H-22: Trivy filesystem-mode scan. Fails the build on any CRITICAL
  # finding. HIGH / MEDIUM / LOW are reported (stdout) but don't fail
  # — Dependabot + trivy-cron pick those up on a weekly cadence.
  - name: Trivy scan (CRITICAL gate)
    uses: aquasecurity/trivy-action@<PINNED_SHA> # vX.Y.Z — see Step 2 for the exact SHA + tag
    with:
      image-ref: ghcr.io/bolin8017/${{ inputs.image }}@${{ steps.meta.outputs.digest }}
      severity: CRITICAL
      exit-code: 1
      ignore-unfixed: false
      format: table

  # H-22: Generate an SPDX SBOM and attach it as a Cosign-compatible
  # attestation. Runs only on push (PR builds skip — the image isn't
  # in a registry to attach to).
  - name: Generate SBOM (SPDX)
    if: inputs.push == 'true'
    uses: anchore/sbom-action@<PINNED_SHA> # vX.Y.Z — see Step 2 for the exact SHA + tag
    with:
      image: ghcr.io/bolin8017/${{ inputs.image }}@${{ steps.meta.outputs.digest }}
      format: spdx-json
      output-file: sbom.spdx.json
      upload-artifact: true
      upload-release-assets: false
  ```

- [ ] **Step 2: Pin `aquasecurity/trivy-action` and `anchore/sbom-action` to 40-char commit SHAs.**

  Per `.claude/rules/github-actions.md`, every `uses:` reference must be a 40-char SHA with a same-line comment naming the release tag. Look up the current stable releases:

  ```bash
  # Get the latest stable release tag + its commit SHA for each action.
  gh api repos/aquasecurity/trivy-action/releases/latest --jq '{tag: .tag_name, sha: .target_commitish}'
  gh api repos/anchore/sbom-action/releases/latest        --jq '{tag: .tag_name, sha: .target_commitish}'
  # If target_commitish returns a branch name (e.g. "main") instead of a SHA,
  # resolve to the commit SHA at the tag:
  gh api repos/aquasecurity/trivy-action/git/refs/tags/<tag> --jq .object.sha
  gh api repos/anchore/sbom-action/git/refs/tags/<tag>        --jq .object.sha
  ```

  Paste the resolved 40-char SHAs in place of `<PINNED_SHA>`, with the tag in the same-line comment, e.g.:

  ```yaml
  uses: aquasecurity/trivy-action@76071ef0aa3838387c47d3ca1ec9d6f8c5e29b6e # v0.30.0
  ```

- [ ] **Step 3: Verify the docker-meta-build composite renders.**

  ```bash
  python3 -c 'import yaml; yaml.safe_load(open(".github/actions/docker-meta-build/action.yml"))' && echo "YAML OK"
  ```

  Expected: `YAML OK`.

- [ ] **Step 4: Smoke-test via workflow_dispatch on a feature branch.**

  Push the feature branch + trigger `images.yml` manually:

  ```bash
  git push -u origin security-hardening-p4
  gh workflow run images.yml --ref security-hardening-p4
  gh run watch  # follow the run until completion
  ```

  Expected: the `Trivy scan (CRITICAL gate)` step succeeds (assuming current backend / frontend digests have no CRITICAL). The `Generate SBOM (SPDX)` step is skipped because `push: false` on PR-mode dispatch.

  If Trivy fails on a known CRITICAL in the base image: bump the base-image digest in `backend/Dockerfile` or `frontend/Dockerfile` first (Dependabot's incoming PR will land that anyway) before re-running.

- [ ] **Step 5: Commit.**

  ```bash
  git add .github/actions/docker-meta-build/action.yml
  git commit -m "$(cat <<'EOF'
  feat(ci): trivy CRITICAL gate + SPDX SBOM attestation in docker-meta-build [H-22]

  Every push (main + semver tag) now (a) Trivy-scans the built image
  and fails on any CRITICAL finding, (b) attaches an SPDX SBOM as a
  cosign-compatible attestation. PRs run the Trivy scan against the
  locally-built image (no push) for fast CRITICAL feedback before
  merge; SBOM attestation skipped on PR because the image isn't
  registry-resident. trivy-action + sbom-action pinned by 40-char SHA
  per .claude/rules/github-actions.md.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 8: [H-23] Append cosign keyless sign to `docker-meta-build`

**Findings:** H-23 (HIGH). Recommended model: **opus** (OIDC permissions + keyless flow are load-bearing).

**Files:**

- Modify: `.github/actions/docker-meta-build/action.yml`
- Modify: `.github/workflows/images.yml` (grant `id-token: write` to job)
- Modify: `.github/workflows/helpers.yml` (grant `id-token: write` to job)

**Rationale:** Trivy + SBOM (T7) prove the bytes are clean at push time but not WHO pushed them. Cosign keyless sign uses the GHA OIDC token as a short-lived identity (`token.actions.githubusercontent.com` issuer); Sigstore Fulcio mints a 10-minute X.509 cert tied to the workflow identity (`https://github.com/bolin8017/lolday/.github/workflows/images.yml@refs/heads/main`); Rekor logs the signature into the public transparency log. Anyone with `cosign verify --certificate-identity-regexp '...' --certificate-oidc-issuer '...' <image-digest>` can independently confirm the image was published by this repo's `main` (or a semver tag) and nothing else. Kyverno (T10) admission-checks the same identity at cluster-entry.

- [ ] **Step 1: Grant `id-token: write` at the job level in the two consumer workflows.**

  In `.github/workflows/images.yml`, find the `permissions:` block at the job level (lines 32–34):

  ```yaml
  permissions:
    contents: read
    packages: write
  ```

  Add `id-token: write`:

  ```yaml
  permissions:
    contents: read
    packages: write
    id-token: write # H-23: required by cosign keyless OIDC flow
  ```

  Same edit in `.github/workflows/helpers.yml` (lines 30–32).

- [ ] **Step 2: Append the cosign install + sign steps to `docker-meta-build/action.yml`.**

  After the SBOM step (end of file), append:

  ```yaml
  # H-23: install cosign for keyless signing.
  - name: Install cosign
    if: inputs.push == 'true'
    uses: sigstore/cosign-installer@<PINNED_SHA> # vX.Y.Z — Step 3 resolves the SHA

  # H-23: keyless sign the just-pushed image digest. OIDC token is
  # auto-acquired from the GHA OIDC issuer via id-token: write (granted
  # at the job level in images.yml / helpers.yml). --yes skips the
  # interactive prompt; --recursive signs every platform variant of a
  # multi-arch manifest.
  - name: Cosign sign (keyless)
    if: inputs.push == 'true'
    shell: bash
    run: |
      cosign sign --yes --recursive \
        "ghcr.io/bolin8017/${{ inputs.image }}@${{ steps.meta.outputs.digest }}"
    env:
      COSIGN_EXPERIMENTAL: "1" # keyless flow (Sigstore-mainstream)
  ```

- [ ] **Step 3: Pin `sigstore/cosign-installer` by 40-char SHA.**

  ```bash
  gh api repos/sigstore/cosign-installer/releases/latest --jq '{tag: .tag_name, sha: .target_commitish}'
  # If target_commitish is a branch name, resolve to the tag's commit SHA:
  gh api repos/sigstore/cosign-installer/git/refs/tags/<tag> --jq .object.sha
  ```

  Paste the SHA + tag-as-comment.

- [ ] **Step 4: Smoke-test via workflow_dispatch on `main`.**

  Cosign sign only runs on push (PRs don't push). To smoke-test, the operator must merge T1–T7 first so that the next `main` push or semver tag triggers a real signed publish. As an interim, push a tag to a side branch is the closest signal:

  ```bash
  # ASSUMING the feature branch security-hardening-p4 has all of T1-T7+T8.
  # The workflow already runs on main push; merge the PR and observe.
  ```

  After the first successful signed publish, verify on any host with cosign installed (the operator workstation is sufficient):

  ```bash
  cosign verify \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github\.com/bolin8017/lolday/\.github/workflows/(images|helpers)\.yml@refs/(heads/main|tags/v[0-9]+\.[0-9]+\.[0-9]+)$' \
    ghcr.io/bolin8017/lolday-backend@<DIGEST>
  ```

  Expected: a JSON output ending with `[{"Critical":{...},"Optional":{...}}]` and a 0 exit code. If it errors with `no matching signatures`, the cosign sign step did NOT run — check the GHA log for the `Cosign sign (keyless)` step.

- [ ] **Step 5: Commit.**

  ```bash
  git add .github/actions/docker-meta-build/action.yml .github/workflows/images.yml .github/workflows/helpers.yml
  git commit -m "$(cat <<'EOF'
  feat(ci): cosign keyless sign every pushed image digest [H-23]

  Every main / tag push now signs the resulting image digest using the
  GHA OIDC token (issuer token.actions.githubusercontent.com). Fulcio
  mints a short-lived cert tied to the workflow identity; Rekor logs
  the signature into the public transparency log. PR builds skip
  signing (no push). id-token: write granted at the job level in
  images.yml + helpers.yml. Verify post-push with:

    cosign verify \\
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \\
      --certificate-identity-regexp '^https://github\\.com/bolin8017/lolday/\\.github/workflows/(images|helpers)\\.yml@refs/(heads/main|tags/v[0-9]+\\.[0-9]+\\.[0-9]+)$' \\
      <digest>

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 9: [H-23-cluster a] Add Kyverno as a Helm sub-chart

**Findings:** H-23-cluster (HIGH, install slice). Recommended model: **opus** (sub-chart wiring + bootstrap order is the load-bearing decision).

**Files:**

- Modify: `charts/lolday/Chart.yaml` (dependencies)
- Modify: `charts/lolday/values.yaml` (new top-level `kyverno:` block)
- (Auto-generated) `charts/lolday/charts/kyverno-*.tgz` via `helm dependency update`

**Rationale:** Kyverno installs as a sub-chart of `charts/lolday` alongside the existing seven sub-charts (harbor, kps, loki, alloy, trivy-operator, volcano, minio). Pinning version `~3.2.0` (current CNCF Incubating stable) follows the same `~MAJOR.MINOR.PATCH` pattern as the other sub-charts. Kyverno's own controllers live in their own namespace (default `kyverno`); the verifyImages policy (T10) scopes match to lolday + lolday-jobs only, which keeps Kyverno's own image upgrade unobstructed (see D2 — Kyverno bootstrap order). `webhook.failurePolicy: Fail` is the upstream chart default and the mainstream production setting.

- [ ] **Step 1: Add Kyverno to `charts/lolday/Chart.yaml` dependencies.**

  Append to the `dependencies:` block (after the `minio` entry, lines 38–41):

  ```yaml
  - name: kyverno
    version: "~3.2.0"
    repository: https://kyverno.github.io/kyverno/
    condition: kyverno.enabled
  ```

- [ ] **Step 2: Add the `kyverno:` block to `charts/lolday/values.yaml`.**

  Append to `values.yaml` (top-level, after the existing `minio:` block or alongside other sub-chart blocks):

  ```yaml
  # =============================================================================
  # Kyverno — admission-time image verification + PSS background audit (P4)
  # =============================================================================
  # The verifyImages ClusterPolicy in templates/policies/verify-images.yaml
  # admits only images whose cosign signature matches the lolday GHCR workflow
  # identity. Scoped to `lolday` + `lolday-jobs` namespaces — kyverno's own
  # controllers in `kyverno` ns are excluded, so a subsequent kyverno upgrade
  # cannot be rejected by its own webhook (see D2 in plans/2026-05-12-
  # security-hardening-p4-supply-chain.md).
  #
  # Mainstream prod settings: failurePolicy: Fail (block admissions if kyverno
  # is unreachable). PSS enforcement from P2 (built-in Pod Security admission
  # labels on lolday/lolday-jobs namespaces) stays in place; this sub-chart
  # adds the audit-mode PSS background scan as defense-in-depth.
  kyverno:
    enabled: true
    fullnameOverride: kyverno
    # admissionController.replicas: default is 3; lab cluster has 1 node so
    # spreading is moot. Drop to 1 to save memory; admission webhook traffic
    # is gated by the kube-apiserver retry on failure.
    admissionController:
      replicas: 1
      # Mainstream: Fail if kyverno is unreachable. lolday and lolday-jobs
      # admissions block — matches prod-grade installs.
      failurePolicy: Fail
    backgroundController:
      replicas: 1
    reportsController:
      replicas: 1
    cleanupController:
      replicas: 1
  ```

- [ ] **Step 3: Helm-dependency-update + lint.**

  ```bash
  helm dependency update charts/lolday
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: the helm-dependency-update emits a line like `Saving 8 charts` (was 7 before). Lint passes with `0 chart(s) failed`.

- [ ] **Step 4: helm-template to confirm Kyverno templates render.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | grep -E '^kind: (Deployment|ClusterRole|MutatingWebhookConfiguration|ValidatingWebhookConfiguration)' | grep -i kyverno
  ```

  Expected: a non-empty list of Kyverno-prefixed resources.

- [ ] **Step 5: Commit.**

  ```bash
  git add charts/lolday/Chart.yaml charts/lolday/Chart.lock charts/lolday/values.yaml
  git commit -m "$(cat <<'EOF'
  feat(charts): add kyverno ~3.2.0 sub-chart for image verification [H-23-cluster]

  Kyverno joins the existing seven sub-charts (harbor, kps, loki, alloy,
  trivy-operator, volcano, minio). Mainstream prod settings:
  failurePolicy: Fail at the admission webhook. The verifyImages
  ClusterPolicy (next task) admission-checks lolday + lolday-jobs only —
  kyverno's own namespace is excluded so subsequent upgrades cannot be
  blocked by their own webhook. PSS background audit folds in alongside
  P2's K8s built-in Pod Security admission labels (defense-in-depth).

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 10: [H-23-cluster b] Add Kyverno `verifyImages` `ClusterPolicy` for the cosign signature

**Findings:** H-23-cluster (HIGH, policy slice). Recommended model: **opus** (regex + attestor pattern are load-bearing).

**Files:**

- Create: `charts/lolday/templates/policies/verify-images.yaml`
- Create: `charts/lolday/templates/policies/pss-baseline-audit.yaml`

**Rationale:** Kyverno's `verifyImages` rule fails admission for any matching Pod whose image cannot be cosign-verified against the configured attestor. The policy matches GHCR origin (`ghcr.io/bolin8017/lolday-*`) so Harbor-origin runtime images pass through unverified (D1 — GHCR-only signing); the keyless attestor uses the GHA OIDC issuer + cert-identity regex from D3. The policy is scoped to lolday + lolday-jobs namespaces (D2). A second policy turns on PSS background audit so Kyverno's background scans surface PSS Baseline drift alongside the K8s built-in Pod Security admission labels from P2 (defense-in-depth, no enforce-mode change).

- [ ] **Step 1: Create `charts/lolday/templates/policies/verify-images.yaml`.**

  ```yaml
  {{- if .Values.kyverno.enabled }}
  apiVersion: kyverno.io/v1
  kind: ClusterPolicy
  metadata:
    name: verify-lolday-image-signatures
    annotations:
      policies.kyverno.io/title: Verify lolday GHCR image signatures
      policies.kyverno.io/category: Supply Chain Security
      policies.kyverno.io/severity: high
      policies.kyverno.io/description: >
        Every image pulled from ghcr.io/bolin8017/lolday-* must be cosign-signed
        by the lolday GHA workflow identity (images.yml or helpers.yml at
        main or a semver tag). Harbor-origin images are intentionally
        out of scope (D1 — GHCR-only signing). See:
        docs/superpowers/plans/2026-05-12-security-hardening-p4-supply-chain.md
  spec:
    validationFailureAction: Enforce
    webhookTimeoutSeconds: 30
    background: false  # signature check needs network egress to fulcio/rekor
    rules:
      - name: verify-ghcr-cosign-signature
        match:
          any:
            - resources:
                kinds:
                  - Pod
                namespaces:
                  - lolday
                  - lolday-jobs
        verifyImages:
          - imageReferences:
              - "ghcr.io/bolin8017/lolday-*"
            attestors:
              - count: 1
                entries:
                  - keyless:
                      subject: "https://github.com/bolin8017/lolday/.github/workflows/images.yml@refs/heads/main"
                      issuer: "https://token.actions.githubusercontent.com"
                      rekor:
                        url: "https://rekor.sigstore.dev"
                  # Same workflow, semver tag push:
                  - keyless:
                      subjectRegExp: "^https://github\\.com/bolin8017/lolday/\\.github/workflows/images\\.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$"
                      issuer: "https://token.actions.githubusercontent.com"
                      rekor:
                        url: "https://rekor.sigstore.dev"
                  # Helpers workflow, main push + semver tag push:
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
            mutateDigest: false  # don't rewrite tag-only refs; T3 has already pinned digests
            verifyDigest: true
            required: true
  {{- end }}
  ```

  **Notes for the implementer:**
  - The four `entries:` listed under one `attestors[0]` with `count: 1` is the Kyverno-mainstream way to express OR: ANY ONE keyless entry matching is sufficient. The four-entry expansion is more readable than a single big `subjectRegExp` covering all four cases.
  - `mutateDigest: false` is intentional. Kyverno can optionally rewrite tag-only image refs to digest-pinned ones, but H-21-img (T1-T4) has already pinned every ref by digest — making Kyverno's mutation a no-op. Keeping it off means a future ref that drops the digest by accident is REJECTED rather than silently fixed.
  - `verifyDigest: true` means: the in-cluster image manifest digest must match what Cosign signed. Combined with H-21-img digest pinning, this is end-to-end content integrity.
  - `background: false` because keyless verify requires network egress to Fulcio + Rekor; running it as background scans on every existing Pod every 1h is expensive. Admission-time only.

- [ ] **Step 2: Create `charts/lolday/templates/policies/pss-baseline-audit.yaml`.**

  ```yaml
  {{- if .Values.kyverno.enabled }}
  apiVersion: kyverno.io/v1
  kind: ClusterPolicy
  metadata:
    name: pss-baseline-audit-lolday
    annotations:
      policies.kyverno.io/title: PSS Baseline audit (lolday + lolday-jobs)
      policies.kyverno.io/category: Pod Security Standards
      policies.kyverno.io/severity: medium
      policies.kyverno.io/description: >
        Audit-mode PSS Baseline check, complementing the K8s built-in Pod
        Security admission labels installed in P2. Background scans surface
        drift over time; admission enforcement stays with the built-in PSA
        labels (no double-enforcement, fewer false positives).
  spec:
    validationFailureAction: Audit
    background: true
    rules:
      - name: baseline-host-namespaces
        match:
          any:
            - resources:
                kinds:
                  - Pod
                namespaces:
                  - lolday
                  - lolday-jobs
        validate:
          podSecurity:
            level: baseline
            version: latest
  {{- end }}
  ```

- [ ] **Step 3: helm-template the new policies and confirm they render.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | grep -A 2 'kind: ClusterPolicy' | head -20
  ```

  Expected: two `kind: ClusterPolicy` entries with names `verify-lolday-image-signatures` and `pss-baseline-audit-lolday`.

- [ ] **Step 4: Confirm helm lint stays clean.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `0 chart(s) failed`.

- [ ] **Step 5: After deploy (operator action), verify Kyverno admission rejects an unsigned image.**

  This step runs only after `bash scripts/deploy.sh` lands T1–T10 onto the cluster. From the operator's workstation:

  ```bash
  # Try to apply a Pod with an unsigned image in lolday namespace.
  cat <<'EOF' | kubectl apply -f -
  apiVersion: v1
  kind: Pod
  metadata:
    namespace: lolday
    name: kyverno-verifyimages-smoketest
  spec:
    containers:
      - name: c
        image: ghcr.io/bolin8017/lolday-backend:NONEXISTENT
    restartPolicy: Never
  EOF
  ```

  Expected: `Error from server: error when creating "...": admission webhook "ivpolicy.kyverno.svc-fail" denied the request: ...` — admission REJECTED. If admission accepts (Pod created), the policy is not in effect; check `kubectl get clusterpolicy verify-lolday-image-signatures -o yaml` for `status.conditions[].status: True` on `Ready`.

- [ ] **Step 6: Commit.**

  ```bash
  git add charts/lolday/templates/policies/verify-images.yaml charts/lolday/templates/policies/pss-baseline-audit.yaml
  git commit -m "$(cat <<'EOF'
  feat(charts): kyverno verifyImages policy + PSS audit ClusterPolicies [H-23-cluster]

  Two ClusterPolicies under charts/lolday/templates/policies/:
    - verify-lolday-image-signatures: cosign keyless verify on every
      Pod in lolday + lolday-jobs whose image matches ghcr.io/bolin8017/
      lolday-*. Four attestor entries (images.yml main, images.yml tags,
      helpers.yml main, helpers.yml tags) under one OR-attestor block.
      validationFailureAction: Enforce. Harbor-origin images intentionally
      out of scope (D1 — GHCR-only signing).
    - pss-baseline-audit-lolday: PSS Baseline audit-mode background scan
      complementing P2's built-in PSA labels (defense-in-depth, no
      double-enforce).

  Post-deploy smoke test in the task body: try to apply an unsigned image
  in lolday ns; expect "admission webhook ivpolicy.kyverno.svc-fail denied
  the request".

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 11: [M-helper-hashes] Require pip install hashes for build-helper, mlflow-server, pytorch-cu12-base

**Findings:** M-helper-hashes (MEDIUM). Recommended model: **opus** (hash-generation flow + pinning semantics matter).

**Files:**

- Create: `charts/lolday/helpers/build-helper/requirements.txt`
- Create: `charts/lolday/helpers/mlflow-server/requirements.txt`
- Create: `charts/lolday/helpers/pytorch-cu12-base/requirements-runtime.txt`
- Modify: `charts/lolday/helpers/build-helper/Dockerfile`
- Modify: `charts/lolday/helpers/mlflow-server/Dockerfile`
- Modify: `charts/lolday/helpers/pytorch-cu12-base/Dockerfile`

**Rationale:** Today the three helper Dockerfiles run `pip install --no-cache-dir <pkg>==X.Y.Z` — version-pinned but **not hash-pinned**. A registry compromise or MITM that swaps `maldet-2.0.tar.gz` for a tampered wheel under the same version goes undetected because pip's default install verifies signatures only when packages are signed by PyPI (most are not). `pip install --require-hashes -r requirements.txt` requires every line in the file to include a `--hash=sha256:<digest>` clause; pip refuses to install if any hash mismatches. Generate the hashed requirements via `uv pip compile --generate-hashes`, which resolves the full transitive closure and emits exact + hash + index-url per line. Job-helper is NOT covered here — its dependencies (`httpx`, `mlflow-skinny`) come from `pyproject.toml` + `uv.lock` (which already pins hashes); the Dockerfile installs the local package, not a requirements file.

- [ ] **Step 1: Generate hashed requirements for build-helper.**

  From the operator's host (uv available):

  ```bash
  cd charts/lolday/helpers/build-helper
  # Pin everything the Dockerfile installs. build-helper imports only
  # maldet[lightning] per pyproject.toml deps.
  uv pip compile --generate-hashes \
    --output-file requirements.txt \
    pyproject.toml
  ```

  Expected: a `requirements.txt` like:

  ```
  # This file was autogenerated by uv via the following command:
  #    uv pip compile --generate-hashes --output-file requirements.txt pyproject.toml
  maldet==2.0.5 \
      --hash=sha256:abc... \
      --hash=sha256:def...
  pytorch-lightning==2.4.0 \
      --hash=sha256:...
  # ... full transitive closure ...
  ```

  Optionally inspect the file to confirm `maldet[lightning]>=2.0,<3.0` resolved to a concrete `==X.Y.Z`.

- [ ] **Step 2: Update `charts/lolday/helpers/build-helper/Dockerfile`.**

  Replace lines 3–6:

  ```dockerfile
  RUN apt-get update && apt-get install -y --no-install-recommends \
          ca-certificates \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir 'maldet[lightning]>=2.0,<3.0'
  ```

  with:

  ```dockerfile
  COPY requirements.txt /tmp/requirements.txt

  # M-helper-hashes: --require-hashes means pip refuses any install whose
  # downloaded wheel/sdist hash differs from the value pinned in
  # requirements.txt. Generated via `uv pip compile --generate-hashes`.
  RUN apt-get update && apt-get install -y --no-install-recommends \
          ca-certificates \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
      && rm /tmp/requirements.txt
  ```

- [ ] **Step 3: Generate hashed requirements for mlflow-server.**

  ```bash
  cd ../mlflow-server
  # mlflow-server installs psycopg2-binary==2.9.9 + boto3==1.38.5 on top of
  # the upstream ghcr.io/mlflow/mlflow base. Use a stub pyproject.toml-less
  # approach: write the two deps to a temp file and resolve with uv.
  cat > /tmp/mlflow-server-deps.txt <<'EOF'
  psycopg2-binary==2.9.9
  boto3==1.38.5
  EOF
  uv pip compile --generate-hashes \
    --output-file requirements.txt \
    /tmp/mlflow-server-deps.txt
  rm /tmp/mlflow-server-deps.txt
  ```

  Expected: `requirements.txt` with the two pins + their hashes + transitive (botocore, s3transfer, etc.).

- [ ] **Step 4: Update `charts/lolday/helpers/mlflow-server/Dockerfile`.**

  Replace lines 3–6:

  ```dockerfile
  # Upstream ghcr.io mlflow image is slim and omits Postgres and S3 drivers.
  # - psycopg2-binary: backend-store-uri `postgresql+psycopg2://...`
  # - boto3: S3 artifact store (MinIO via MLFLOW_S3_ENDPOINT_URL)
  RUN pip install --no-cache-dir psycopg2-binary==2.9.9 boto3==1.38.5
  ```

  with:

  ```dockerfile
  # Upstream ghcr.io mlflow image is slim and omits Postgres and S3 drivers.
  # M-helper-hashes: hash-pinned requirements file generated via
  # `uv pip compile --generate-hashes`. Pip refuses any install whose
  # downloaded artifact hash differs from the pin.
  COPY requirements.txt /tmp/requirements.txt
  RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
      && rm /tmp/requirements.txt
  ```

- [ ] **Step 5: Generate hashed requirements for pytorch-cu12-base (runtime-only).**

  The pytorch-cu12-base Dockerfile installs torch + torchvision from a custom index URL AND a baseline scientific stack from PyPI. Hashes for the torch wheels can be generated; the index-url constraint flows through. **Caveat:** `uv pip compile` resolves against PyPI by default; pass `--extra-index-url` to teach it about the cu126 wheel index.

  ```bash
  cd ../pytorch-cu12-base
  cat > /tmp/pytorch-deps.txt <<'EOF'
  --index-url https://download.pytorch.org/whl/cu126
  --extra-index-url https://pypi.org/simple/
  torch==2.7.0
  torchvision==0.22.0
  numpy==2.2.3
  pandas==2.2.3
  scikit-learn==1.6.1
  pyelftools==0.32
  structlog==25.1.0
  typer==0.15.2
  psutil==6.1.1
  pynvml==12.0.0
  islab-malware-detector[mlflow]==0.5.0
  EOF
  uv pip compile --generate-hashes \
    --output-file requirements-runtime.txt \
    /tmp/pytorch-deps.txt
  rm /tmp/pytorch-deps.txt
  ```

- [ ] **Step 6: Update `charts/lolday/helpers/pytorch-cu12-base/Dockerfile`.**

  The Dockerfile has TWO `pip install` blocks (lines 44–47 for torch, lines 55–64 for the scientific stack). Collapse both into one `--require-hashes` install using `requirements-runtime.txt`:

  Replace lines 42–64:

  ```dockerfile
  # Torch 2.7.0 with CUDA 12.6 wheel set (matches host driver 560 which
  # caps at CUDA 12.6). torch >=2.6 required for CVE-2025-32434.
  RUN pip install --no-cache-dir \
          torch==2.7.0 \
          torchvision==0.22.0 \
          --index-url https://download.pytorch.org/whl/cu126

  # Baseline scientific stack every detector uses. All pinned exact so
  # the Trivy scan of this base image is reproducible across rebuilds.
  #
  # psutil + pynvml: required by MLflow 2.8+ system metrics logging
  # (MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING in detector pod spec). Without
  # both, mlflow silently no-ops the system/* metrics. Spec § 5.6.
  RUN pip install --no-cache-dir \
          numpy==2.2.3 \
          pandas==2.2.3 \
          scikit-learn==1.6.1 \
          pyelftools==0.32 \
          structlog==25.1.0 \
          typer==0.15.2 \
          psutil==6.1.1 \
          pynvml==12.0.0 \
          "islab-malware-detector[mlflow]==0.5.0"
  ```

  with:

  ```dockerfile
  # M-helper-hashes: hash-pinned runtime requirements (torch wheel set,
  # baseline scientific stack, islab-malware-detector). Generated via
  # `uv pip compile --generate-hashes` against the cu126 wheel index +
  # PyPI fallback. Pip refuses any install whose artifact hash differs
  # from the pin — defense against the registry-republish-under-same-
  # version attack vector that version-only pinning doesn't catch.
  #
  # psutil + pynvml: required by MLflow 2.8+ system metrics logging
  # (MLFLOW_ENABLE_SYSTEM_METRICS_LOGGING). Without both, mlflow silently
  # no-ops system/* metrics. Spec § 5.6.
  COPY requirements-runtime.txt /tmp/requirements-runtime.txt
  RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.txt \
      && rm /tmp/requirements-runtime.txt
  ```

- [ ] **Step 7: Build each helper to confirm pip accepts the hashes.**

  On a host with docker buildx:

  ```bash
  docker build -t test-build-helper-hashes charts/lolday/helpers/build-helper/
  docker build -t test-mlflow-hashes      charts/lolday/helpers/mlflow-server/
  docker build -t test-pytorch-hashes     charts/lolday/helpers/pytorch-cu12-base/
  ```

  Expected: each build succeeds. If pip errors with `ERROR: Hashes are required ... when --require-hashes is in use`, the requirements file is missing a transitive dep — re-run `uv pip compile` with explicit `--all-extras` if relevant.

- [ ] **Step 8: Commit.**

  ```bash
  git add charts/lolday/helpers/build-helper/{Dockerfile,requirements.txt} \
          charts/lolday/helpers/mlflow-server/{Dockerfile,requirements.txt} \
          charts/lolday/helpers/pytorch-cu12-base/{Dockerfile,requirements-runtime.txt}
  git commit -m "$(cat <<'EOF'
  feat(helpers): pip install --require-hashes for build-helper, mlflow-server, pytorch-cu12-base [M-helper-hashes]

  Each helper Dockerfile now installs from a `uv pip compile --generate-
  hashes`-generated requirements file with --require-hashes. Pip refuses
  installs whose downloaded artifact hash differs from the pin —
  defense against the same-version-republish attack that version-only
  pinning can't catch. job-helper unchanged: its Dockerfile installs
  the local package from pyproject.toml + uv.lock (which already pins
  hashes via uv's resolver).

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 12: [M-pytorch-bootstrap] Replace `curl get-pip.py` with `python3.12 -m ensurepip`

**Findings:** M-pytorch-bootstrap (MEDIUM). Recommended model: **sonnet** (single Dockerfile line).

**Files:**

- Modify: `charts/lolday/helpers/pytorch-cu12-base/Dockerfile:36`

**Rationale:** Today the Dockerfile bootstraps pip via `curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12`. The `get-pip.py` URL is mutable — bootstrap.pypa.io serves whatever pip release the upstream maintainer publishes; an attacker who compromises that endpoint or MITMs the in-build HTTPS handshake (still possible behind a permissive corp proxy) gets root-level code exec inside the image build. `python3.12 -m ensurepip --upgrade` ships with the deadsnakes-provided python3.12 package itself — already byte-pinned by apt's package signature plus T3's @sha256 digest on the base image. Zero network egress, zero attack surface.

- [ ] **Step 1: Modify the apt-install block at lines 25–40.**

  Replace the line:

  ```dockerfile
      && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12 \
  ```

  with:

  ```dockerfile
      && python3.12 -m ensurepip --upgrade \
  ```

  Same block, the `curl` package is now no longer used for pip bootstrap. Check if `curl` is used elsewhere in the helper layers — `grep curl charts/lolday/helpers/pytorch-cu12-base/Dockerfile`. If `curl` is only used by the deleted line, drop it from `apt-get install`:

  ```dockerfile
  RUN apt-get update \
      && apt-get upgrade -y \
      && apt-get install -y --no-install-recommends \
           software-properties-common ca-certificates \
      && add-apt-repository -y ppa:deadsnakes/ppa \
      && apt-get update \
      && apt-get install -y --no-install-recommends \
           python3.12 python3.12-venv python3.12-dev \
           libgomp1 \
      && ln -sf /usr/bin/python3.12 /usr/local/bin/python \
      && ln -sf /usr/bin/python3.12 /usr/local/bin/python3 \
      && python3.12 -m ensurepip --upgrade \
      && ln -sf /usr/local/bin/pip3 /usr/local/bin/pip \
      && apt-get purge -y software-properties-common \
      && apt-get autoremove -y \
      && rm -rf /var/lib/apt/lists/* /root/.cache/pip
  ```

  (`curl` removed from the install list.)

- [ ] **Step 2: Build to confirm.**

  ```bash
  docker build -t test-pytorch-ensurepip charts/lolday/helpers/pytorch-cu12-base/
  docker run --rm test-pytorch-ensurepip python3.12 -m pip --version
  ```

  Expected: the build succeeds and pip version prints (e.g., `pip 24.X from /usr/local/lib/...`).

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/helpers/pytorch-cu12-base/Dockerfile
  git commit -m "$(cat <<'EOF'
  fix(helpers): pytorch-cu12-base bootstraps pip via ensurepip, not curl get-pip.py [M-pytorch-bootstrap]

  `curl https://bootstrap.pypa.io/get-pip.py | python3.12` was the
  build-time RCE vector: bootstrap.pypa.io is a mutable URL endpoint
  serving an attacker-controlled script if compromised. `python3.12 -m
  ensurepip --upgrade` ships with the deadsnakes python3.12 package
  itself — already byte-pinned by apt signature + the @sha256 base image
  pin from H-21-img. Zero network egress for pip bootstrap. `curl`
  dropped from the apt install list since it is now unused.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 13: [M-codecov-gate] Gate codecov upload to push or same-repo PR

**Findings:** M-codecov-gate (MEDIUM). Recommended model: **sonnet** (one `if:` line).

**Files:**

- Modify: `.github/workflows/backend.yml:47-54`

**Rationale:** Today the `Upload coverage to Codecov` step runs unconditionally on every PR. A fork PR that opens against the lolday repo can request `secrets.CODECOV_TOKEN`; GHA's fork-PR security model normally blocks secret access, but if a future contributor's fork gains push access (or a token is leaked in build logs), the codecov upload becomes an exfiltration vector. Gating to `github.event_name == 'push'` OR `github.event.pull_request.head.repo.full_name == github.repository` runs the upload only for trusted origins.

- [ ] **Step 1: Add the `if:` clause to the Codecov step.**

  In `.github/workflows/backend.yml`, modify lines 47–54 from:

  ```yaml
  - name: Upload coverage to Codecov
    uses: codecov/codecov-action@57e3a136b779b570ffcdbf80b3bdc90e7fab3de2 # v6.0.0
    with:
      files: ./backend/coverage.xml
      flags: backend
      fail_ci_if_error: false
    env:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
  ```

  to:

  ```yaml
  # M-codecov-gate: skip the upload on fork PRs (they cannot
  # access CODECOV_TOKEN anyway, but the step previously ran
  # unconditionally — an attacker who gains write access to a
  # contributor's fork could redirect coverage data. Gate to
  # push events + same-repo PRs only.
  - name: Upload coverage to Codecov
    if: github.event_name == 'push' || github.event.pull_request.head.repo.full_name == github.repository
    uses: codecov/codecov-action@57e3a136b779b570ffcdbf80b3bdc90e7fab3de2 # v6.0.0
    with:
      files: ./backend/coverage.xml
      flags: backend
      fail_ci_if_error: false
    env:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
  ```

- [ ] **Step 2: YAML lint.**

  ```bash
  python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/backend.yml"))' && echo "YAML OK"
  ```

  Expected: `YAML OK`.

- [ ] **Step 3: Commit.**

  ```bash
  git add .github/workflows/backend.yml
  git commit -m "$(cat <<'EOF'
  fix(ci): gate codecov upload to push or same-repo PR [M-codecov-gate]

  GHA's fork-PR security model normally blocks secret access on fork
  PRs, but the codecov step previously ran unconditionally — if a future
  contributor's fork gains write access (or CODECOV_TOKEN leaks via a
  build log echo), the upload becomes an exfiltration sink. Gate to
  push events + same-repo PRs only.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 14: [M-trivy-cron] Weekly Trivy scan workflow for Dependabot-excluded images

**Findings:** M-trivy-cron (MEDIUM). Recommended model: **opus** (workflow + issue-opening logic + identity is load-bearing).

**Files:**

- Create: `.github/workflows/trivy-cron.yml`

**Rationale:** Dependabot tracks four Dockerfiles (`/backend`, `/frontend`, `/charts/lolday/helpers/build-helper`, `/charts/lolday/helpers/job-helper`); `mlflow-server` and `pytorch-cu12-base` are intentionally excluded because tag bumps need a coordinated review. But the digest re-anchor pattern still applies — upstream republishes `ghcr.io/mlflow/mlflow:v2.20.3` whenever a CVE patch lands. Without Dependabot, those updates go unnoticed. A weekly Trivy cron scans the current pinned digest of those two helpers and opens an issue on CRITICAL, prompting the operator to manually bump the digest + rebuild + repush. The workflow uses the same Trivy action SHA as T7 for consistency.

- [ ] **Step 1: Create `.github/workflows/trivy-cron.yml`.**

  ````yaml
  name: trivy-cron

  on:
    schedule:
      # Mondays 09:00 UTC = 17:00 Asia/Taipei. Aligns with Dependabot's
      # weekly Monday schedule so all security signals land in the
      # operator's PR review queue on the same day.
      - cron: "0 9 * * 1"
    workflow_dispatch:

  permissions:
    contents: read
    issues: write

  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: false

  jobs:
    scan-excluded-bases:
      runs-on: ubuntu-24.04
      strategy:
        fail-fast: false
        matrix:
          # Each entry names the upstream base image referenced by the
          # respective helper Dockerfile after H-21-img digest pinning.
          # Updating this list when the helper's FROM changes is the
          # operator's job — Dependabot doesn't touch these helpers.
          image:
            - name: mlflow-server-base
              ref: "ghcr.io/mlflow/mlflow:v2.20.3"
            - name: pytorch-cu12-base
              ref: "nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04"
      steps:
        - name: Trivy scan (report only)
          id: trivy
          uses: aquasecurity/trivy-action@<PINNED_SHA> # vX.Y.Z — same SHA as T7
          with:
            image-ref: ${{ matrix.image.ref }}
            severity: CRITICAL,HIGH
            format: table
            exit-code: 0 # never fail; we open an issue instead
            output: trivy-${{ matrix.image.name }}.txt

        - name: Check for CRITICAL findings
          id: gate
          shell: bash
          run: |
            if grep -qE "^Total:.*[1-9]+[0-9]*\s+\(.*CRITICAL: [1-9]" trivy-${{ matrix.image.name }}.txt; then
              echo "has_critical=true" >> "$GITHUB_OUTPUT"
            else
              echo "has_critical=false" >> "$GITHUB_OUTPUT"
            fi

        - name: Open issue on CRITICAL
          if: steps.gate.outputs.has_critical == 'true'
          uses: actions/github-script@<PINNED_SHA> # vX.Y.Z — resolve like T7
          with:
            script: |
              const fs = require('fs');
              const body = fs.readFileSync('trivy-${{ matrix.image.name }}.txt', 'utf8');
              github.rest.issues.create({
                owner: context.repo.owner,
                repo: context.repo.repo,
                title: `[trivy-cron] CRITICAL findings on ${{ matrix.image.ref }}`,
                body: '```\n' + body + '\n```\n\nManual action: bump the FROM digest in `charts/lolday/helpers/<helper>/Dockerfile`, rebuild, repush, update values.yaml @sha256 pin (T1/T2 process), open PR.',
                labels: ['security', 'trivy-cron'],
              });
  ````

- [ ] **Step 2: Pin `aquasecurity/trivy-action` and `actions/github-script` by SHA.**

  Use the same `aquasecurity/trivy-action` SHA captured in T7. For `actions/github-script`:

  ```bash
  gh api repos/actions/github-script/releases/latest --jq '{tag: .tag_name, sha: .target_commitish}'
  gh api repos/actions/github-script/git/refs/tags/<tag> --jq .object.sha
  ```

  Substitute the two `<PINNED_SHA>` placeholders + add the tag-as-comment.

- [ ] **Step 3: YAML lint.**

  ```bash
  python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/trivy-cron.yml"))' && echo "YAML OK"
  ```

  Expected: `YAML OK`.

- [ ] **Step 4: Smoke-test via workflow_dispatch.**

  ```bash
  git push -u origin security-hardening-p4
  gh workflow run trivy-cron.yml --ref security-hardening-p4
  gh run watch
  ```

  Expected: scan completes; an issue is opened ONLY if the current digest has CRITICALs. Close the test issue manually after verification.

- [ ] **Step 5: Commit.**

  ```bash
  git add .github/workflows/trivy-cron.yml
  git commit -m "$(cat <<'EOF'
  feat(ci): weekly Trivy cron for Dependabot-excluded helper bases [M-trivy-cron]

  mlflow-server and pytorch-cu12-base base images are excluded from
  Dependabot per their existing rationale (tag bumps need coordinated
  review). A weekly Trivy cron scans the current digest of both bases
  and opens an issue labeled `security`,`trivy-cron` on any CRITICAL
  finding — the operator manually bumps the FROM digest, rebuilds,
  repushes, opens a PR. Mondays 09:00 UTC = 17:00 Asia/Taipei matches
  Dependabot's weekly cadence so all security signals land on the same
  day.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 15: [M-harbor-sha-validate] Regex-guard `harbor_has_tag`'s SHA input

**Findings:** M-harbor-sha-validate (MEDIUM). Recommended model: **sonnet** (~5-line edit + test).

**Files:**

- Modify: `scripts/build-helpers.sh::harbor_has_tag` (around line 142)
- Test: ad-hoc test in the task body (bash unit-test pattern)

**Rationale:** `harbor_has_tag $name $sha` today uses `$sha` directly in the URL query string `q=tags=$sha`. If `$sha` is somehow contaminated (e.g., the upstream caller passes user input or a fragment like `; rm -rf /`), the Harbor API receives a malformed query and either errors or returns a misleading result. Regex-guarding `$sha` to `^[0-9a-f]{6,64}$` (the docker tag SHA range from short-12 up to full sha256) makes the function fail closed on any non-SHA argument, eliminating the input-injection class entirely.

- [ ] **Step 1: Modify `scripts/build-helpers.sh::harbor_has_tag`.**

  At the top of the function (right after the local variable declarations around line 135), add:

  ```bash
    # M-harbor-sha-validate: $sha must be a docker tag SHA — short-12
    # subtree SHA (build-helpers convention) or full 64-char sha256
    # digest. Anything else is an input contamination bug; fail closed.
    if [[ ! "$sha" =~ ^[0-9a-f]{6,64}$ ]]; then
      echo "ERROR: harbor_has_tag refusing non-SHA arg: $sha" >&2
      return 2
    fi
  ```

- [ ] **Step 2: Confirm the guard fires.**

  Source the script and call the function with a malicious argument:

  ```bash
  ( source scripts/build-helpers.sh
    harbor_has_tag build-helper '; rm -rf /' && echo "BUG: guard accepted bad input"
  ) 2>&1 | head -3
  ```

  Expected: `ERROR: harbor_has_tag refusing non-SHA arg: ; rm -rf /` and exit 2; `BUG:` does NOT print.

- [ ] **Step 3: Confirm the guard does NOT fire on a valid 12-char subtree SHA.**

  ```bash
  ( source scripts/build-helpers.sh
    harbor_has_tag build-helper 9f45d263d9d2; echo "exit=$?"
  )
  ```

  Expected: the function executes (may return 0 or 1 depending on whether the tag exists; both are acceptable — the guard does NOT fire). Exit code is 0 or 1, NOT 2.

- [ ] **Step 4: Commit.**

  ```bash
  git add scripts/build-helpers.sh
  git commit -m "$(cat <<'EOF'
  fix(scripts): regex-guard harbor_has_tag's $sha argument [M-harbor-sha-validate]

  $sha flows directly into the Harbor REST API query string. If the
  caller passes contaminated input (e.g., a fragment like `; rm -rf /`
  via a hypothetical future caller that doesn't sanitize), the function
  silently issues a malformed query and may return a misleading result.
  Regex-guard $sha to ^[0-9a-f]{6,64}$ (short-12 subtree SHA up to full
  sha256) and fail closed (return 2) on mismatch. No legitimate caller
  is affected.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 16: [L-mlflow-user] `USER 1000` in `mlflow-server/Dockerfile`

**Findings:** L-mlflow-user (LOW). Recommended model: **sonnet** (3-line edit).

**Files:**

- Modify: `charts/lolday/helpers/mlflow-server/Dockerfile`
- Possibly Modify: `charts/lolday/templates/mlflow.yaml` (securityContext alignment — verify with template inspection)

**Rationale:** Today the mlflow-server image inherits root from its upstream base (`ghcr.io/mlflow/mlflow:v2.20.3`). The pod-level `securityContext.runAsNonRoot: true` from PSS Restricted (P2) would refuse to launch this pod if it weren't running under `lolday` namespace's PSS Baseline carve-out — the Restricted level requires both runAsNonRoot AND a numeric runAsUser. Setting `USER 1000` at the image level (a) makes the image self-consistent across any namespace, (b) drops privilege without relying on chart-level `securityContext` overrides, (c) aligns mlflow's runtime user with backend's (UID 1000 = `lolday`).

- [ ] **Step 1: Modify `charts/lolday/helpers/mlflow-server/Dockerfile`.**

  Append after the existing `RUN pip install ...` line:

  ```dockerfile
  # L-mlflow-user: drop privilege at image level. Upstream mlflow base
  # leaves USER unset (= root). MLflow's runtime writes to its tracking
  # store ($MLFLOW_BACKEND_STORE_URI, Postgres) and its artifact store
  # ($MLFLOW_S3_ENDPOINT_URL, MinIO); neither requires UID 0. Align with
  # backend's lolday user (UID 1000).
  RUN useradd -m -u 1000 -s /usr/sbin/nologin mlflow || true
  USER 1000
  ```

  (The `|| true` covers the case where the upstream base already has UID 1000 — `useradd` would fail with exit 9; the `||` keeps the layer build idempotent.)

- [ ] **Step 2: helm-template the mlflow Deployment and confirm securityContext.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test 2>/dev/null \
    | yq 'select(.kind == "Deployment" and .metadata.name == "mlflow") | .spec.template.spec.securityContext'
  ```

  Expected: at minimum `{runAsNonRoot: true, runAsUser: 1000}` or equivalent — verify against `charts/lolday/templates/mlflow.yaml`. If the chart-level securityContext doesn't already set `runAsUser: 1000`, add it now (this is a Helm-template edit, not Dockerfile):

  ```yaml
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
  ```

- [ ] **Step 3: Build to confirm.**

  ```bash
  docker build -t test-mlflow-user charts/lolday/helpers/mlflow-server/
  docker run --rm test-mlflow-user id
  ```

  Expected: `uid=1000(mlflow) gid=1000(mlflow) groups=1000(mlflow)`.

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/helpers/mlflow-server/Dockerfile charts/lolday/templates/mlflow.yaml
  git commit -m "$(cat <<'EOF'
  fix(helpers): mlflow-server runs as UID 1000 [L-mlflow-user]

  Upstream ghcr.io/mlflow/mlflow:v2.20.3 leaves USER unset (= root).
  MLflow's runtime never needs UID 0 (backend store is Postgres, artifact
  store is MinIO over S3). USER 1000 at the image level + matching
  securityContext in templates/mlflow.yaml align mlflow's runtime user
  with backend's `lolday` user. PSS Restricted is satisfied without
  relying on chart-level overrides.

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## P4 Done

After Task 16 lands, verify the whole phase end-to-end:

- [ ] **Step A: Full backend test suite.**

  ```bash
  cd backend && uv run pytest -q
  ```

  Expected: green (no new backend tests added by P4 — the phase is CI / chart / Dockerfile).

- [ ] **Step B: helm lint (post-T9 includes Kyverno).**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: clean.

- [ ] **Step C: helm-template digest-presence audit (acceptance criterion #1).**

  ```bash
  git grep -E "^[[:space:]]*image:" charts/lolday/values.yaml | grep -vE "@sha256:[0-9a-f]{64}" | grep -vE "registry\.enabled" | head
  ```

  Expected: empty (the only excluded line is the disabled `registry:` block under `registry.enabled: false`, which is acceptable — never rendered into a Pod).

- [ ] **Step D: pre-commit on all files.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean. **Do NOT use `--no-verify`** (per project hard rule).

- [ ] **Step E: Cross-check finding IDs in commit history.**

  ```bash
  git log --oneline main..HEAD | grep -oE '\[[A-Z][^]]+\]' | tr ',' '\n' | sort -u | tr -d '[]'
  ```

  Expected output (set):

  ```
  H-21-img
  H-22
  H-23
  H-23-cluster
  L-mlflow-user
  M-cache-poison
  M-codecov-gate
  M-harbor-sha-validate
  M-helper-hashes
  M-pytorch-bootstrap
  M-trivy-cron
  ```

- [ ] **Step F: Post-deploy operator verification.**

  After `bash scripts/deploy.sh` lands the chart with Kyverno enabled:

  ```bash
  # Acceptance criterion #2: PR with CRITICAL CVE fails the Trivy gate.
  # (Simulated via a feature branch that pins an old digest.) Confirm
  # the images.yml run for the PR shows the Trivy step red.

  # Acceptance criterion #3: cosign verifies the latest backend digest.
  BACKEND_DIGEST=$(kubectl -n lolday get deploy backend -o jsonpath='{.spec.template.spec.containers[0].image}' | grep -oE 'sha256:[0-9a-f]+')
  cosign verify \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    --certificate-identity-regexp '^https://github\.com/bolin8017/lolday/\.github/workflows/(images|helpers)\.yml@refs/(heads/main|tags/v[0-9]+\.[0-9]+\.[0-9]+)$' \
    "ghcr.io/bolin8017/lolday-backend@${BACKEND_DIGEST}"
  # Expected: a JSON envelope + exit 0.

  # Acceptance criterion #4: Kyverno admission rejects an unsigned image.
  cat <<'EOF' | kubectl apply -f - 2>&1 | head -3
  apiVersion: v1
  kind: Pod
  metadata:
    namespace: lolday
    name: kyverno-deny-smoketest
  spec:
    containers:
      - name: c
        image: ghcr.io/bolin8017/lolday-backend:NONEXISTENT
    restartPolicy: Never
  EOF
  # Expected: "Error from server: ... admission webhook ivpolicy.kyverno.svc-fail denied the request: ..."

  # Acceptance criterion #5: pnpm audit clean.
  (cd frontend && pnpm audit --prod --json | jq '[.advisories[] | select(.severity | IN("high","critical"))] | length')
  # Expected: 0
  ```

- [ ] **Step G: Open the PR.**

  Push the branch + `gh pr create --base main`. PR body must call out:
  - **Breaking deploy expectation:** Operator must merge the PR _and then_ run `bash scripts/deploy.sh` so Kyverno installs before subsequent image admissions are evaluated. If the deploy is reverted with Kyverno's verifyImages already active and the new chart bytes not yet rolled, lolday + lolday-jobs admissions will block. Mitigate: order matters — ship + deploy + verify in the same operator window.
  - **Trust root:** every prod image (lolday-owned + helpers) is now content-addressed by `@sha256:<digest>`. Harbor-origin images remain operator-pushed; only GHCR-origin images are cosign-signed and Kyverno-verified.
  - **Operator action post-merge — verify cosign signatures:** `cosign verify --certificate-identity-regexp '...' ghcr.io/bolin8017/lolday-backend@<latest digest>` should succeed on each image.
  - **Operator action post-merge — schedule weekly review:** triage any `trivy-cron`-labeled issue opened by `.github/workflows/trivy-cron.yml`.
  - **No data migrations.** No backend code path changed. No detector behavior change.

---

## Notes for the implementer

- **GHA OIDC `id-token: write` scope.** This grant is per-job, not per-workflow. T8 explicitly adds it to the two consumer workflows' job-level `permissions:` block — composite actions cannot grant OIDC access on their callers' behalf.
- **`docker buildx imagetools inspect` requires authenticated access to private registries.** For the Harbor digest capture in T1 + T2, run on server30 (where kubectl + Harbor robot creds already authenticate). For GHCR digests (T8 verify), use `gh auth setup-git && docker login ghcr.io -u $(gh api user --jq .login) -p $(gh auth token)` on the operator workstation.
- **Cosign keyless flow gotchas.** `cosign sign --yes` in CI works only when GHA OIDC is enabled at the job level (T8 Step 1). Locally, `cosign sign` falls back to an interactive OAuth flow that pops a browser — fine for ad-hoc signing of an emergency rebuild, but should not be the default path. Always sign in CI.
- **Kyverno + Helm hooks ordering.** Kyverno's upstream chart installs CRDs via `helm.sh/hook: pre-install,pre-upgrade`. If `helm upgrade --install` lands the chart for the first time with our `verify-images.yaml` ClusterPolicy in the same release, Kyverno's CRDs land first via the pre-install hook, then the ClusterPolicy applies — no manual ordering needed. Subsequent upgrades follow the same path.
- **Trivy false positives.** Trivy's CRITICAL classification follows NVD CVSS 3.x base score >= 9.0. Some upstream images carry CRITICAL on a transitive dependency that lolday doesn't exercise (e.g., a Pillow CVE in a base image where Pillow isn't imported). When that happens, the right answer is to bump the base-image digest to a newer upstream rebuild — NOT to add a Trivy ignore comment.
- **`pip --require-hashes` caveat for torch CUDA wheels.** Step 5 of T11 passes `--index-url https://download.pytorch.org/whl/cu126` plus `--extra-index-url https://pypi.org/simple/`. `uv pip compile` resolves against both indices and emits hashes from whichever index served the wheel. The hash itself is content-addressable, so the index URL at install-time is verified by the hash.
- **Per-task TDD note.** P4 is mostly CI / chart / Dockerfile, not backend code. The TDD discipline lands as: (a) hook script changes (T1, T2) have ad-hoc bash regression tests in the task body; (b) Kyverno policies (T10) have a post-deploy smoke test that submits an unsigned Pod and asserts admission rejection; (c) docker-meta-build composite changes (T6, T7, T8) are smoke-tested via `gh workflow run` on a feature branch.
- **Model selection per task** (recommended; pass via `--model` to subagent):
  - **sonnet** — T3, T5, T6, T12, T13, T15, T16 (single-file edits, comment refresh, regex tweak)
  - **opus** — T1, T2, T4, T7, T8, T9, T10, T11, T14 (multi-file, security primitives, attestor regex, policy bootstrap)

---

## Self-review (writing-plans skill)

**Spec coverage** — every P4 finding from spec §6.4 maps to one or more tasks:

| Finding               | Tasks                                                                                                                      |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| H-21-img              | T1 (values.yaml lolday-own), T2 (helpers.lock), T3 (Dockerfile FROMs), T4 (values.yaml sub-charts), T5 (Dependabot review) |
| H-22                  | T7 (Trivy + SBOM)                                                                                                          |
| H-23                  | T8 (cosign sign)                                                                                                           |
| H-23-cluster          | T9 (Kyverno install), T10 (verifyImages + PSS audit)                                                                       |
| M-cache-poison        | T6                                                                                                                         |
| M-helper-hashes       | T11                                                                                                                        |
| M-pytorch-bootstrap   | T12                                                                                                                        |
| M-codecov-gate        | T13                                                                                                                        |
| M-trivy-cron          | T14                                                                                                                        |
| M-harbor-sha-validate | T15                                                                                                                        |
| L-mlflow-user         | T16                                                                                                                        |

11 spec findings, 16 implementation tasks. All five spec-level acceptance criteria (`§6.4`) traceable:

| Spec acceptance                                                                                                                            | Plan check                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| 1. `git grep "image:" values.yaml \| grep -vE "@sha256:"` → empty                                                                          | T1 + T4 + Step C in P4 Done                         |
| 2. PR with CRITICAL CVE fails Trivy step                                                                                                   | T7 Step 4 + Step F (criterion 2)                    |
| 3. `cosign verify --certificate-identity-regexp 'https://github.com/bolin8017/lolday/.*' ghcr.io/bolin8017/lolday-backend@sha256:...` → OK | T8 Step 4 + Step F (criterion 3)                    |
| 4. `kubectl apply -f` unsigned image → Kyverno rejects                                                                                     | T10 Step 5 + Step F (criterion 4)                   |
| 5. `pnpm audit --prod --json` → 0 high/critical                                                                                            | Step F (criterion 5) — passive, no plan task needed |

**Placeholder scan:**

- `<BACKEND_DIGEST_BARE>` / `<FRONTEND_DIGEST_BARE>` / `<MLFLOW_DIGEST_BARE>` / `<PYTHON_314_DIGEST>` / `<NODE_22_DIGEST>` / `<NGINX_UNPRIV_DIGEST>` / `<PYTHON_312_DIGEST>` / `<MLFLOW_BASE_DIGEST>` / `<NVIDIA_CUDA_DIGEST>` / `<CLOUDFLARED_DIGEST>` / `<POSTGRES_DIGEST>` / `<REDIS_DIGEST>` / `<PG_EXPORTER_DIGEST>` / `<HARBOR_*_DIGEST>` × 10 / `<LOKI_SIDECAR_DIGEST>` are operator-captured at the corresponding `docker buildx imagetools inspect` step, not plan placeholders — the plan instructs the implementer how to derive each one in the same Step.
- `<PINNED_SHA>` placeholders in T7 / T8 / T14 are resolved via the explicit `gh api repos/.../releases/latest` + `gh api repos/.../git/refs/tags/<tag>` flow in the task body. Not a plan failure — the plan tells the implementer where to look.
- No `TBD` / `implement later` / `add appropriate ...` markers.

**Type consistency:**

- `helpers.lock` shape change (T2) — keys remain `build_helper` + `job_helper`, values gain `@sha256:<digest>` suffix. `deploy.sh` consumes via `python3 -c 'import json; print(...)'` — string-only consumer, no schema constraint.
- `harbor_has_tag` (T15) — adds regex guard at function entry; return code semantics: 0 = exists, 1 = not exists (HTTP 404), 2 = error (HTTP non-200 or invalid input). T15 reuses the existing `return 2` slot, no caller change needed.
- Kyverno `verifyImages.attestors[].entries[].keyless` (T10) — four entries under a `count: 1` attestor; mainstream OR-expression pattern. `subject` for the main-push case (literal match), `subjectRegExp` for the semver-tag case (regex). Both supported in Kyverno 3.x.
- Cosign `--certificate-identity-regexp` (T8 + Step F) — `\.` escape applied in the regex (Bash needs `\\.`, but in the shell string literal the single backslash is taken). The plan body shows the regex exactly as it will be passed to cosign verify.

**Known fragilities:**

- **T4 sub-chart digest pinning** — relies on each upstream sub-chart's template rendering `{{.repository}}:{{.tag}}` and accepting `tag` as an opaque string. If a future Harbor / loki chart upgrade switches to validating `tag` as semver (rejecting `:v2.15.0@sha256:...`), T4 must be reworked to use `image.repository: <name>@sha256:<digest>` instead. Tracked: revisit when bumping the Harbor sub-chart to 1.19+.
- **T7 Trivy false-positive escapes** — Trivy's NVD source occasionally classifies fixed-but-not-released CVEs as CRITICAL. The plan does not provide a Trivy ignore-list path; the implementer's escape valve is bumping the base-image digest (Dependabot picks this up on the same week). If a CRITICAL persists across multiple weekly cycles with no upstream fix, file a follow-up ticket — do NOT add `--severity HIGH` carve-outs without operator sign-off.
- **T10 Kyverno admission bootstrap on fresh install** — if the cluster's NetworkPolicy denies kyverno → fulcio.sigstore.dev or kyverno → rekor.sigstore.dev egress, every verify call fails open or rejects (depending on the policy's `failurePolicy`). Lolday's existing NetworkPolicy templates do not gate egress to public Sigstore. If a future P-series tightens cluster egress, the verify-images policy MUST be exempted or signature verification will silently break.
- **T11 `--require-hashes` and PyTorch cu126 wheels** — the cu126 wheel index occasionally re-publishes wheels under the same filename when patching ABI compatibility (rare; observed once in 2025-12). When that happens, the next CI rebuild fails with `pip install --require-hashes` hash-mismatch. Operator workflow: re-run `uv pip compile --generate-hashes` against the new wheel set, commit the updated `requirements-runtime.txt`, redeploy. No plan-time mitigation possible.
- **T2 `docker buildx imagetools inspect` against Harbor** — Harbor's manifest digest equals what `docker push` reports, but the buildx tool must trust Harbor's TLS cert. Harbor on server30 runs HTTP (`harbor.lolday.svc:80`); buildx accepts the digest over plain HTTP if the registry is in `daemon.json::insecure-registries`. The existing setup already has Harbor in the insecure list, so this is a no-op. If a future host hardens to HTTPS-only, T2's digest capture path must adapt.
