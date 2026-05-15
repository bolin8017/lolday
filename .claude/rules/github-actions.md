# GitHub Actions rules

Path scope: anything under `.github/`.

## Action pinning

All `uses:` references **MUST pin a 40-character commit SHA** with a same-line comment naming the release tag. Floating tags (`@v4`, `@main`) are forbidden — tags are repo-mutable and a compromised upstream can swap them. SHAs cannot be force-rewritten.

```yaml
# good
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

# forbidden
- uses: actions/checkout@v4
- uses: actions/checkout@main
```

Dependabot (`.github/dependabot.yml`, ecosystem: github-actions) bumps SHA + comment in lock-step weekly. Do not hand-edit unless Dependabot is broken.

## Permissions

Every workflow declares an explicit `permissions:` block. Default `read-all` is forbidden — least privilege.

- read-only workflows: `permissions: { contents: read }` at workflow level.
- workflows that push to GHCR (`images.yml`, `helpers.yml`): `contents: read` workflow-level + `packages: write` job-level (not workflow-level), and the push step itself gates on `if: github.event_name != 'pull_request'`.

## `pull_request_target` is forbidden

`pull_request_target` runs the workflow with the base ref's secrets but with the PR's code — historic supply-chain attack vector. Use plain `pull_request` only. If a future need arises, write a root-cause justification in the PR description and require operator sign-off; do not introduce silently.

## Single source of truth

Lint/format/typecheck/lock checks are owned by `.pre-commit-config.yaml`. CI runs `pre-commit run --all-files` in `lint.yml`. Do **not** add parallel `uv run ruff check` / `pnpm lint` / etc. steps to other workflows — duplication breeds drift.

Adding a new lint hook: edit `.pre-commit-config.yaml`. CI follows automatically.

## Secret scanning + supply chain

Three orthogonal gates run on PRs into `main`:

- `gitleaks.yml` — secret-scan gate via `gitleaks/gitleaks-action`. Config + allowlist: repo-root `.gitleaks.toml`. Seeded ahead of the 2026-05-15 public flip. Required full git history (`fetch-depth: 0`).
- **GitHub Secret Scanning + Push Protection** — repo-level setting (no in-repo yaml). Catches leaks on push BEFORE they hit the workflow. Verify via Settings → Code security and analysis.
- **Dependabot Security Updates** — repo-level setting, separate from the `dependabot.yml` version-bump scheduler. Auto-opens PRs against high-severity advisories on the existing `pip` / `npm` / `docker` / `github-actions` ecosystems.

Image-signing + provenance (`docker-meta-build` composite): cosign sign + `actions/attest-build-provenance` on every `main` / tag push. Verified at admission by the Kyverno `verify-lolday-image-signatures` ClusterPolicy (GHCR keyless) and `verify-lolday-harbor-image-signatures` (Harbor key-based). See `.claude/rules/charts-and-helm.md` §Top-level templates and `docs/runbooks/kyverno-bootstrap.md` / `docs/runbooks/kyverno-harbor-signing.md`.

## Branch protection

Active on `main` since 2026-05-15: PR required; no force-push; no delete; linear history. `required_approving_review_count: 0` (single-operator project). Full ruleset + admin-merge precedent: `docs/conventions.md` §10.6.

## Two-registry model

- `ghcr.io/bolin8017/lolday-*` — CI artifact registry. PR builds verify; `main` and tag pushes publish.
- `harbor.lolday.svc:80/lolday/*` — production runtime registry, server30-internal. Populated by operator running `bash scripts/build-helpers.sh` (and parallel manual flows for backend / frontend / mlflow-server / pytorch-cu12-base).

CI never pushes to Harbor. Spec rationale: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md` §3.1.

## Adding a new image

Add a matrix entry to the appropriate workflow:

- backend / frontend / new platform image → `images.yml` `matrix.image`.
- helper-class image → `helpers.yml` `matrix.helper`. **`mlflow-server` and `pytorch-cu12-base` are out of scope** (external base images, low-frequency updates, large body — operator manual). Adding them requires updating the spec first.

Do not create a new workflow file per image — the matrix pattern is the mainstream way.

Every image added via the matrix automatically inherits cosign sign + SLSA `attest-build-provenance` from the `docker-meta-build` composite. **Do not** publish to Harbor from CI — Harbor pushes are operator-driven via `scripts/build-helpers.sh` (which signs by digest against `~/.cosign/lolday-harbor.key`).

## Adding a new ecosystem to Dependabot

Edit `.github/dependabot.yml`. Do not bypass with hand-edits to lockfiles.

## Composite actions

Three composites under `.github/actions/`:

- `setup-uv` — wraps `astral-sh/setup-uv` + `uv sync --frozen --project <dir>`.
- `setup-pnpm-node` — corepack + setup-node (with pnpm cache) + `pnpm --dir frontend install --frozen-lockfile`.
- `docker-meta-build` — buildx + conditional GHCR login + metadata-action + build-push-action with per-image GHA cache scope, followed by `cosign sign` (GHCR keyless via GHA OIDC) + `actions/attest-build-provenance` for SLSA L3 attestation on `main` / tag pushes. The `syft` SBOM step is currently disabled (private-GHCR scan + SPDX-JSON mix breaks the action's stdout parsing; see auto-memory `project_syft_ghcr_sbom_disabled.md`).

Use `${{ github.token }}` (not `env.GITHUB_TOKEN`) inside composites — env is not auto-inherited.

## Concurrency

Every workflow declares:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

PR pushes auto-cancel the prior run on the same ref. `main` and tag runs never auto-cancel (release artefacts protected).

## Runner pin

`runs-on: ubuntu-24.04`. **Not** `ubuntu-latest` (silently rolls major).

## Path filtering

Each workflow except `lint.yml` carries a `paths` or `paths-ignore` filter — see each workflow's header. Tag pushes ignore path filters by GHA design; that's intentional for release.

## Forbidden additions

- `actionlint` as a separate pre-commit hook in this phase (scope kept tight; pre-commit's `check-yaml` plus GHA's own parser cover known failure modes).
- Self-hosted runners.
- Inline secret literals in workflow yaml.
- `${{ env.GITHUB_TOKEN }}` inside composite actions (use `${{ github.token }}`).
