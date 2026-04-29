# GitHub Actions CI/CD — Design Specification

## Overview

Lolday currently has **no automated CI**. `docs/architecture.md` §9 #2 records this as the largest outstanding tech debt: no `.github/workflows/`, no automated build / test / lint / image build pipeline. All hygiene runs locally via `pre-commit run --all-files`; nothing enforces it on PR. Operators verify by hand before each `bash scripts/deploy.sh`.

This phase introduces a complete GitHub Actions pipeline that:

1. **Enforces engineering hygiene on every PR** by running `pre-commit run --all-files` (single source of truth — same `.pre-commit-config.yaml` operators run locally).
2. **Verifies tests / typecheck / build** for backend, frontend, helm chart, and helper images.
3. **Publishes container images to GHCR** (`ghcr.io/bolin8017/lolday-*`) on `main` and tag pushes — purely as a CI artifact / traceability layer. Production registry (Harbor) is unchanged: operator continues to use `bash scripts/build-helpers.sh` and `bash scripts/deploy.sh` on server30.
4. **Pins all third-party actions to commit SHA** with Dependabot keeping them fresh — mainstream supply-chain hygiene.

CI is verification + artifact only. **Production deploy automation is explicitly out of scope** — Harbor lives only on server30's internal network and exposing it for CI push is rejected as a root-cause anti-pattern (see §3.1 below).

## Authorization

Breaking changes are explicitly authorized:

- The wording of `docs/architecture.md` §6 «No CI/CD» and §9 #2 «No CI/CD» is rewritten in place — those sections cease to describe the current state.
- Trivially-affecting metadata (badges, conventions doc) added to `README.md` and `docs/conventions.md`.

No breaking changes to existing developer flow:

- `bash scripts/deploy.sh`, `bash scripts/build-helpers.sh`, `pre-commit run --all-files` all keep their current behavior unchanged.
- No app code touched.

## Scope

### In scope

1. **Six workflow files** under `.github/workflows/`: `lint.yml`, `backend.yml`, `frontend.yml`, `helm.yml`, `images.yml`, `helpers.yml`.
2. **Three composite actions** under `.github/actions/`: `setup-uv`, `setup-pnpm-node`, `docker-meta-build`.
3. **`.github/dependabot.yml`** covering `github-actions`, `pip`, `npm`, `docker` ecosystems.
4. **GHCR publishing** for backend / frontend / build-helper / job-helper on `main` and `v*.*.*` tags.
5. **Path-filtered triggering** so unrelated changes (docs / single-area edits) skip irrelevant workflows.
6. **Documentation updates**: README badge bar, new `docs/conventions.md` §CI/CD, mark `docs/architecture.md` §6 + §9 #2 as resolved, new `.claude/rules/github-actions.md` (path-scoped to `.github/`), cross-links from existing area rules.
7. **Branch-protection setup guide** in `docs/conventions.md` (operator manually configures in GitHub UI — GitHub provides no in-repo declarative API).

### Out of scope

- Production deploy automation. Harbor push, `scripts/deploy.sh` invocation, K3s rollout — all stay manual, run on server30 only.
- Playwright E2E on PR. A `playwright-e2e` job slot is left commented-out in `frontend.yml` with a TODO comment pointing at this spec; future phase activates it.
- `mlflow-server` and `pytorch-cu12-base` helper image CI build. Their tags carry external semantic meaning, body sizes are large (CUDA base ~5 GB), and update frequency is low. Operator continues manual build.
- Coverage upload (codecov / coveralls). Not blocked by this spec; can be a 3-line follow-up.
- Container image signing (cosign / sigstore). ISLab internal use case does not require it now.
- Container image vulnerability scan (trivy on built image in CI). Trivy operator already scans Harbor in production; CI-side trivy is duplicate value. Future.
- GitHub Pages / docs publishing.
- Self-hosted runner. Rejected at design time — see §3.1.
- Automated branch-protection configuration — GitHub does not provide a repo-level declarative branch-protection API stable enough to depend on. Operator follows the runbook in `docs/conventions.md`.

## Architecture

### 3.1 Why GHCR-only (not Harbor)

Harbor is reachable only as `harbor.lolday.svc:80` via server30's `/etc/hosts` plus the K3s containerd registry mirror (`/etc/rancher/k3s/registries.yaml`). It is **internal by design** — `docs/architecture.md` §5.3 documents the two intentional host-name forms; neither is publicly resolvable. GitHub-hosted runners (`ubuntu-24.04` in GitHub Cloud) cannot push to Harbor without one of:

| Option                                                            | Why rejected                                                                                                                                                                             |
| ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Expose Harbor through Cloudflare Tunnel + CF Access service token | Adds an external attack surface for a non-essential need; not the architectural intent of `harbor.lolday.svc`.                                                                           |
| Self-hosted runner on server30                                    | Adds a long-running daemon with auto-update behavior to a host whose SSH safety hard rule (`CLAUDE.md`) forbids surprise infra changes. The 2026-03-31 Cilium incident is the precedent. |
| GHA → SSH into server30 → docker push                             | Same self-hosted-runner risk plus credential management.                                                                                                                                 |

Mainstream split-registry pattern is the root-cause solution:

- **GHCR** (`ghcr.io/bolin8017/lolday-*`) — CI artifact, Dependabot-friendly, cloud-native, no production network coupling.
- **Harbor** (`harbor.lolday.svc:80/lolday/*`) — production runtime, server30-internal, populated by operator running `scripts/build-helpers.sh`.

A future GHCR → Harbor mirror (e.g. `regctl image copy` invoked by a server30 cron) is a deferrable enhancement, not a CI-design dependency.

### 3.2 Why pre-commit is the single source of truth

`.pre-commit-config.yaml` already encodes ruff (lint + format) + mypy + prettier + eslint + standard hygiene hooks + `helpers-lock-fresh`. Operators are required to run pre-commit locally; bypassing with `--no-verify` is a hard-rule violation.

CI re-runs the **exact same hooks** via `pre-commit run --all-files`. Adding a parallel set of `uv run ruff check` / `pnpm typecheck` steps in workflow YAML duplicates the rule definitions and lets the two drift. Pydantic, FastAPI, Posthog, and Dagster all use the single-source pattern; we follow.

Trade-off: pre-commit's mypy / prettier / eslint hooks are `language: system` — they need `backend/.venv` + `frontend/node_modules`. The `lint.yml` workflow therefore runs `uv sync` and `pnpm install` before `pre-commit run`. With caching this adds ~5–15 s on a warm cache. Worth it for the single-truth property.

### 3.3 Workflow topology

```
.github/
├── workflows/
│   ├── lint.yml              # pre-commit run --all-files
│   ├── backend.yml           # uv run pytest
│   ├── frontend.yml          # pnpm typecheck + pnpm test (vitest)
│   ├── helm.yml              # helm dep update + lint + template
│   ├── images.yml            # backend / frontend Dockerfile build → GHCR
│   └── helpers.yml           # build-helper / job-helper Dockerfile build → GHCR
├── actions/
│   ├── setup-uv/action.yml         # composite: uv install + uv sync
│   ├── setup-pnpm-node/action.yml  # composite: corepack + setup-node + pnpm install
│   └── docker-meta-build/action.yml # composite: buildx + login + metadata + build/push
└── dependabot.yml            # github-actions + pip + npm + docker
```

No `needs:` dependency between workflows — they run flat and parallel. Branch protection (in GitHub UI) declares all six as required checks.

### 3.4 Trigger model

| Workflow       |  `push: main`  |     `pull_request → main`     | `push: tags v*.*.*` | `workflow_dispatch` |
| -------------- | :------------: | :---------------------------: | :-----------------: | :-----------------: |
| `lint.yml`     |       ✅       |              ✅               |          —          |         ✅          |
| `backend.yml`  |       ✅       |              ✅               |          —          |         ✅          |
| `frontend.yml` |       ✅       |              ✅               |          —          |         ✅          |
| `helm.yml`     |       ✅       |              ✅               |          —          |         ✅          |
| `images.yml`   | ✅ → push GHCR |         ✅ build only         |  ✅ → push semver   |         ✅          |
| `helpers.yml`  | ✅ → push GHCR | ✅ build only (path-filtered) |          —          |         ✅          |

PR builds **never push** any image and **never need** a non-default secret. `GITHUB_TOKEN` write scopes are job-conditional on `github.event_name != 'pull_request'`. PRs from any source (in this private repo, only same-repo branches) cannot exfiltrate any credential.

### 3.5 Path filtering

| Workflow       | `paths` / `paths-ignore`                                                                                                   |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `lint.yml`     | none — runs on every change (cheap, ~30 s warm)                                                                            |
| `backend.yml`  | `paths-ignore: ['**.md', 'docs/**', 'frontend/**', 'charts/**', 'scripts/**']`                                             |
| `frontend.yml` | `paths-ignore: ['**.md', 'docs/**', 'backend/**', 'charts/**', 'scripts/**']`                                              |
| `helm.yml`     | `paths: ['charts/**', '.github/workflows/helm.yml']`                                                                       |
| `images.yml`   | `paths: ['backend/**', 'frontend/**', '.github/workflows/images.yml']`                                                     |
| `helpers.yml`  | `paths: ['charts/lolday/helpers/build-helper/**', 'charts/lolday/helpers/job-helper/**', '.github/workflows/helpers.yml']` |

`mlflow-server` / `pytorch-cu12-base` are deliberately absent from `helpers.yml`'s `paths` — their CI build is out of scope.

### 3.6 Image tag rules

`docker/metadata-action@<sha>` configured for the **mainstream Docker GitHub-Action default tag set**:

| Trigger            | Tags applied                                 |
| ------------------ | -------------------------------------------- |
| `push: main`       | `main`, `main-<short-sha>`, `sha-<long-sha>` |
| `push: tag v1.2.3` | `1.2.3`, `1.2`, `1`, `latest`                |
| `pull_request`     | not pushed (build only)                      |

GHCR namespace: `ghcr.io/bolin8017/lolday-{backend,frontend,build-helper,job-helper}`.

### 3.7 Permissions per workflow (least privilege)

| Workflow       | `contents` |           `packages`           | rationale           |
| -------------- | :--------: | :----------------------------: | ------------------- |
| `lint.yml`     |    read    |               —                | reads code only     |
| `backend.yml`  |    read    |               —                | reads code only     |
| `frontend.yml` |    read    |               —                | reads code only     |
| `helm.yml`     |    read    |               —                | reads code only     |
| `images.yml`   |    read    | write (job-level, conditional) | push GHCR on non-PR |
| `helpers.yml`  |    read    | write (job-level, conditional) | push GHCR on non-PR |

`packages: write` is declared at job-level, never workflow-level, and the push step itself uses `if: github.event_name != 'pull_request'` as a defense-in-depth.

### 3.8 Concurrency

Every workflow declares:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

PR pushes auto-cancel earlier runs on the same ref. `main` and tag runs never cancel each other (release artifacts are protected).

### 3.9 Runner pin

All workflows: `runs-on: ubuntu-24.04`. **Not** `ubuntu-latest` — that label rolls forward and silently inherits new behavior. Pinning matches server30's Ubuntu 24.04 (same glibc / openssl ABI for Dockerfile parity).

### 3.10 Supply-chain pinning

All `uses:` references **pin a 40-character commit SHA** with a same-line comment showing the release tag. Example:

```yaml
- uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11 # v4.2.2
- uses: astral-sh/setup-uv@<sha> # v5.x.y
- uses: docker/build-push-action@<sha> # v6.x.y
```

Reasoning: Git tags can be force-overwritten by their owning repo; commit SHAs cannot. OpenSSF and GitHub's official supply-chain guidance recommend SHA pinning. Dependabot then bumps the SHA + comment in lock-step.

## Workflow detail

### 4.1 `lint.yml`

|             |                                                          |
| ----------- | -------------------------------------------------------- |
| Trigger     | `push: main`, `pull_request → main`, `workflow_dispatch` |
| Runner      | `ubuntu-24.04`                                           |
| Permissions | `contents: read`                                         |
| Job         | `pre-commit`                                             |

Steps:

1. `actions/checkout@<sha>` with **`fetch-depth: 0`**.
   - Required: `helpers-lock-fresh` hook calls `scripts/check-helpers-lock.sh`, which uses `git rev-parse HEAD:charts/lolday/helpers/<name>` to compute subtree SHAs. Shallow clones make tree-object lookup fail (the script's own `assert_not_shallow` errors out).
2. `./.github/actions/setup-uv` (composite) — installs uv and runs `uv sync --frozen --project backend` (mypy hook needs the venv).
3. `./.github/actions/setup-pnpm-node` (composite) — corepack-enables pnpm, runs `pnpm --dir frontend install --frozen-lockfile` (prettier + eslint hooks need `frontend/node_modules`).
4. `actions/cache@<sha>` for `~/.cache/pre-commit` keyed on `${{ runner.os }}-precommit-${{ hashFiles('.pre-commit-config.yaml') }}`.
5. `uv tool install pre-commit` (or use uv-managed pre-commit binary).
6. `pre-commit run --all-files --show-diff-on-failure --color always`.

Failure mode visible to reviewer: pre-commit prints offending file + diff inline; PR check name in GitHub UI is `lint / pre-commit`.

### 4.2 `backend.yml`

|             |                                                                                   |
| ----------- | --------------------------------------------------------------------------------- |
| Trigger     | `push: main`, `pull_request → main`, `workflow_dispatch`, `paths-ignore` per §3.5 |
| Runner      | `ubuntu-24.04`                                                                    |
| Permissions | `contents: read`                                                                  |
| Job         | `pytest`                                                                          |

Steps:

1. `actions/checkout@<sha>` (shallow OK).
2. `./.github/actions/setup-uv` (`uv sync --frozen --project backend`, including dev group).
3. `cd backend && uv run pytest -v --tb=short`.

Postgres service container is intentionally omitted. Tests use aiosqlite (`backend/tests/conftest.py`); MLflow is autouse-mocked; fakeredis covers Redis. Adding Postgres for tests would mask a documented design choice (`docs/architecture.md` §6, `.claude/rules/backend.md`).

### 4.3 `frontend.yml`

|             |                                                                                   |
| ----------- | --------------------------------------------------------------------------------- |
| Trigger     | `push: main`, `pull_request → main`, `workflow_dispatch`, `paths-ignore` per §3.5 |
| Runner      | `ubuntu-24.04`                                                                    |
| Permissions | `contents: read`                                                                  |
| Job         | `unit`                                                                            |

Steps:

1. `actions/checkout@<sha>` (shallow OK).
2. `./.github/actions/setup-pnpm-node`.
3. `pnpm --dir frontend typecheck` (`tsc --noEmit`).
4. `pnpm --dir frontend test` (vitest, `--passWithNoTests` already in `package.json`).

A second job slot `playwright-e2e` is **kept as commented-out YAML** with a TODO referencing this spec — future phase activates after deciding service-container layout (Postgres / Redis / mocked CF Access).

### 4.4 `helm.yml`

|             |                                                                            |
| ----------- | -------------------------------------------------------------------------- |
| Trigger     | `push: main`, `pull_request → main`, `workflow_dispatch`, `paths` per §3.5 |
| Runner      | `ubuntu-24.04`                                                             |
| Permissions | `contents: read`                                                           |
| Job         | `lint-template`                                                            |

Steps:

1. `actions/checkout@<sha>` (shallow OK).
2. `azure/setup-helm@<sha>` pinning Helm 3.x (latest stable that matches `scripts/install-tools.sh`).
3. `actions/cache@<sha>` for `charts/lolday/charts/` keyed on `Chart.lock` hash.
4. `helm dependency update charts/lolday`.
5. `helm lint charts/lolday`.
6. `helm template lolday charts/lolday --namespace lolday > /tmp/manifests.yaml` — render to confirm no template error.
7. `actions/upload-artifact@<sha>` uploads `/tmp/manifests.yaml` (named per run; reviewer can diff template output across PRs if they care).

### 4.5 `images.yml`

|                         |                                                                                                     |
| ----------------------- | --------------------------------------------------------------------------------------------------- |
| Trigger                 | `push: main`, `pull_request → main`, `push: tags ['v*.*.*']`, `workflow_dispatch`, `paths` per §3.5 |
| Runner                  | `ubuntu-24.04`                                                                                      |
| Permissions (job-level) | `contents: read`, `packages: write`                                                                 |
| Job                     | `build-image` (matrix: `image: [backend, frontend]`)                                                |

Steps per matrix entry:

1. `actions/checkout@<sha>` (shallow OK).
2. `./.github/actions/docker-meta-build` with inputs:
   - `image: lolday-${{ matrix.image }}`
   - `context: ./${{ matrix.image }}`
   - `push: ${{ github.event_name != 'pull_request' }}`

The composite handles `setup-buildx`, conditional `login` to `ghcr.io`, `metadata-action` with the §3.6 tag rules, and `build-push-action` with `cache-from / cache-to: type=gha,scope=${{ matrix.image }}` (per-image cache scope so backend / frontend layer changes don't invalidate each other).

### 4.6 `helpers.yml`

Same shape as `images.yml` with matrix `helper: [build-helper, job-helper]`, and `paths` filter (§3.5) excluding `mlflow-server` and `pytorch-cu12-base`. GHCR namespace: `ghcr.io/bolin8017/lolday-${{ matrix.helper }}`.

## Composite actions

### 5.1 `.github/actions/setup-uv/action.yml`

```yaml
name: setup-uv
description: install uv and sync the backend project
inputs:
  working-directory:
    description: project directory passed to `uv sync --project`
    default: backend
runs:
  using: composite
  steps:
    - uses: astral-sh/setup-uv@<sha> # v5.x.y
      with:
        enable-cache: true
        cache-dependency-glob: "**/uv.lock"
    - shell: bash
      run: uv sync --frozen --project ${{ inputs.working-directory }}
```

### 5.2 `.github/actions/setup-pnpm-node/action.yml`

```yaml
name: setup-pnpm-node
description: corepack-enable pnpm, install frontend deps with cache
runs:
  using: composite
  steps:
    - shell: bash
      run: corepack enable
    - uses: actions/setup-node@<sha> # v4.x.y
      with:
        node-version-file: frontend/.nvmrc # NEW — see §6
        cache: pnpm
        cache-dependency-path: frontend/pnpm-lock.yaml
    - shell: bash
      run: pnpm --dir frontend install --frozen-lockfile
```

A `frontend/.nvmrc` file pinning Node major version (`22`) is added in this phase — operator-machine and CI use the same Node major. `package.json` already has `"engines": { "node": ">=22" }`; `.nvmrc` is the file `setup-node` reads.

### 5.3 `.github/actions/docker-meta-build/action.yml`

```yaml
name: docker-meta-build
description: build (and optionally push) a Dockerfile to GHCR with mainstream tag rules
inputs:
  image:
    { description: image short name (without registry/owner), required: true }
  context: { description: build context path, required: true }
  push: { description: "true to push", required: true }
runs:
  using: composite
  steps:
    - uses: docker/setup-buildx-action@<sha> # v3.x.y
    - if: inputs.push == 'true'
      uses: docker/login-action@<sha> # v3.x.y
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ env.GITHUB_TOKEN }}
    - id: meta
      uses: docker/metadata-action@<sha> # v5.x.y
      with:
        images: ghcr.io/bolin8017/${{ inputs.image }}
        tags: |
          type=ref,event=branch
          type=ref,event=branch,suffix=-{{sha}}
          type=sha,format=long
          type=semver,pattern={{version}}
          type=semver,pattern={{major}}.{{minor}}
          type=semver,pattern={{major}}
          type=raw,value=latest,enable={{is_default_branch}}
    - uses: docker/build-push-action@<sha> # v6.x.y
      with:
        context: ${{ inputs.context }}
        push: ${{ inputs.push }}
        tags: ${{ steps.meta.outputs.tags }}
        labels: ${{ steps.meta.outputs.labels }}
        cache-from: type=gha,scope=${{ inputs.image }}
        cache-to: type=gha,scope=${{ inputs.image }},mode=max
```

## Caching summary

| Subject                | Mechanism                                        | Key                                                                       |
| ---------------------- | ------------------------------------------------ | ------------------------------------------------------------------------- |
| `uv` cache             | `astral-sh/setup-uv@<sha>` `enable-cache: true`  | `uv-${{ runner.os }}-${{ hashFiles('**/uv.lock') }}` (provided by action) |
| `pnpm` store           | `actions/setup-node@<sha>` `cache: pnpm`         | provided by action via `pnpm-lock.yaml`                                   |
| Pre-commit hook envs   | `actions/cache@<sha>` on `~/.cache/pre-commit`   | `${{ runner.os }}-precommit-${{ hashFiles('.pre-commit-config.yaml') }}`  |
| Docker buildx layers   | `cache-from / cache-to: type=gha,scope=<image>`  | per-image scope                                                           |
| Helm sub-chart `*.tgz` | `actions/cache@<sha>` on `charts/lolday/charts/` | `${{ runner.os }}-helm-deps-${{ hashFiles('charts/lolday/Chart.lock') }}` |

## Dependabot

`.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule: { interval: weekly, day: monday }
    open-pull-requests-limit: 10
    groups:
      actions-minor-patch:
        update-types: [minor, patch]
  - package-ecosystem: pip
    directory: /backend
    schedule: { interval: weekly, day: monday }
    open-pull-requests-limit: 10
    groups:
      backend-minor-patch:
        update-types: [minor, patch]
  - package-ecosystem: npm
    directory: /frontend
    schedule: { interval: weekly, day: monday }
    open-pull-requests-limit: 10
    groups:
      frontend-minor-patch:
        update-types: [minor, patch]
  - package-ecosystem: docker
    directory: /backend
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /frontend
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /charts/lolday/helpers/build-helper
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /charts/lolday/helpers/job-helper
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /charts/lolday/helpers/mlflow-server
    schedule: { interval: weekly }
  - package-ecosystem: docker
    directory: /charts/lolday/helpers/pytorch-cu12-base
    schedule: { interval: weekly }
```

`mlflow-server` / `pytorch-cu12-base` Dockerfiles are tracked by Dependabot even though their CI build is out of scope — base-image bump PRs still need review even when build runs locally.

## Documentation updates

### 8.1 `README.md`

Add a badge bar under the `# Lolday` heading (six badges: lint, backend, frontend, helm, images, helpers).

### 8.2 `docs/conventions.md` — new §«CI/CD»

Subsections:

1. **CI overview** — six workflows, what each does, how triggers map.
2. **Local vs CI parity** — pre-commit is the single source of truth; CI runs the identical config.
3. **Two-registry model** — GHCR for CI artifacts (`ghcr.io/bolin8017/lolday-*`), Harbor for production runtime (`harbor.lolday.svc:80/lolday/*`); when to reference which.
4. **Image tag rules** — table per §3.6.
5. **Branch protection setup** — operator runbook for GitHub Settings → Branches:
   - Require all six workflows as required status checks.
   - Require linear history.
   - Require conversation resolution.
   - Disallow force-push to `main`.
   - Allow squash-merge only.
6. **Releasing** — `git tag v0.1.0 && git push --tags` triggers `images.yml` / `helpers.yml` semver-tag publish.
7. **Dependabot SOP** — green-CI = squash merge; per-ecosystem caveats (GHA SHA-pin upgrade comment, npm peer-dep, pip lockstep).

### 8.3 `docs/architecture.md`

- §6 «Build / Test / Release» — replace the «No CI/CD» paragraph with a one-paragraph summary linking to `docs/conventions.md` §CI/CD and `.github/workflows/`.
- §9 #2 — rewrite the bullet from «No CI/CD» to «~~No CI/CD~~ — resolved 2026-04-30 in `feat/github-actions-cicd`. CI on `main` enforces lint + tests + image build via `.github/workflows/`. PR link below.» (PR link filled at merge time.)

### 8.4 `.claude/rules/github-actions.md` (NEW, path-scoped to `.github/`)

Rules:

- All `uses:` reference a 40-char commit SHA with a same-line comment naming the release tag. Tags alone are forbidden.
- `permissions:` declared explicitly per workflow (least privilege). Default `read-all` is forbidden.
- `pull_request_target` is forbidden unless a written root-cause justification ships with the change.
- Secrets are never injected into PR-builder steps. Push-to-GHCR steps gate on `github.event_name != 'pull_request'`.
- Adding a new lint hook: edit `.pre-commit-config.yaml`; CI follows automatically. **Do not add a parallel step to `lint.yml`** — pre-commit is the single source of truth.
- Adding a new image: extend matrix in `images.yml` or `helpers.yml`; do not create a new workflow file.
- Adding a new ecosystem to Dependabot: extend `.github/dependabot.yml`; do not bypass it with hand-edits.

### 8.5 Cross-link from existing rules

Append one line to each of `.claude/rules/{backend,frontend,scripts-and-ops,charts-and-helm}.md`:

> CI: enforced by `.github/workflows/{lint,<area>}.yml`. Discipline rules in `.claude/rules/github-actions.md`.

### 8.6 `CLAUDE.md` — Quickstart commands

Append (after the existing pre-commit line):

```bash
gh workflow run lint.yml             # trigger CI sanity from local (needs gh CLI)
```

### 8.7 `docs/runbooks/release-helpers.md`

Rewrite the existing «CI integration sketch» (lines 86–109) to reflect actual state:

> CI builds and pushes helper images to **GHCR** as a verification artifact. Production deploy on server30 still uses `bash scripts/build-helpers.sh` (Harbor push) followed by `bash scripts/deploy.sh`. No automated GHCR → Harbor mirror in this phase.

### 8.8 `.gitignore`

Append:

```
# Local GHA debug artifacts (nektos/act)
/.github/.cache/
```

### 8.9 Not updated

- `docs/runbooks/deploy.md` — deploy flow unchanged.
- `README.md` setup section — CI does not affect day-1 install.
- `docs/runbooks/troubleshooting.md` — no new failure modes affect operators.

## First green-CI bring-up plan

To minimize surprises:

1. Write all yaml on a feature branch.
2. Locally: `pre-commit run --all-files` → must be green before opening PR.
3. Push branch; first PR run is expected to surface real issues (cache-cold path filter typos, hidden hook prerequisites). Fix in the same PR.
4. Once all six workflows green on PR, merge.
5. Operator configures branch protection in GitHub UI per `docs/conventions.md` §CI/CD.

## Risks, edge cases, mitigations

| Risk                                                                        |     Likelihood      |             Impact              | Mitigation                                                                                                                    |
| --------------------------------------------------------------------------- | :-----------------: | :-----------------------------: | ----------------------------------------------------------------------------------------------------------------------------- |
| `pre-commit run --all-files` cold cache slow (≥30 s)                        |  High (first run)   |       PR feedback latency       | `actions/cache@<sha>` for `~/.cache/pre-commit`; warm runs <10 s (D-phase observation).                                       |
| `helpers-lock-fresh` fails on shallow clone                                 | Medium (mis-config) |         `lint.yml` red          | `lint.yml` declares `fetch-depth: 0`. Other workflows stay shallow (cheap).                                                   |
| GHA usage exceeds private-repo free tier                                    |         Low         |          Out-of-pocket          | Path filters + concurrency-cancel. Owner verifies plan tier before merge.                                                     |
| Dependabot PR storm                                                         |       Medium        |        Reviewer fatigue         | `groups: minor-patch` per ecosystem squashes minor / patch into one PR; `open-pull-requests-limit: 10`.                       |
| `docker/build-push-action` GHA cache poisoning (theoretical)                |      Very low       |        Compromised image        | GHA cache is GitHub-hosted, scoped per workflow; non-cross-account. SHA-pinned action prevents action-side compromise.        |
| `frontend/.nvmrc` misalignment with operator machine                        |         Low         | Operator-side install confusion | Set `.nvmrc` to `22` (matches `package.json` engines + Dockerfile `node:22-alpine`); document in `.claude/rules/frontend.md`. |
| Composite `setup-uv` overlaps with upstream `astral-sh/setup-uv`            |         Low         |        Maintenance noise        | Composite is a thin wrapper that adds `uv sync` and ergonomic input default. Mainstream pattern (Pydantic, Posthog).          |
| Pulling all six workflows into branch protection at once blocks first merge |       Medium        | Operator confusion at first PR  | Spec instructs branch protection setup **after** merging the CI PR (chicken-and-egg).                                         |
| Tag push triggers GHCR publish before workflow is ready                     |         Low         |       Half-baked release        | Acceptance criterion #5 explicitly rehearses tag push on a throwaway tag (e.g. `v0.0.0-test`) before the first real release.  |
| GHCR retention drift                                                        |   Low (long-term)   |          Storage bloat          | Future phase: `actions/delete-package-versions` cron with retain-N policy. Out of scope here.                                 |

## Acceptance criteria

This phase is complete when:

1. PR on a feature branch shows **6 workflows green** in the GitHub UI: `lint`, `backend`, `frontend`, `helm`, `images`, `helpers`.
2. CI's `pre-commit run --all-files` produces **identical output** to the operator's local run on the same commit (no CI-only failures, no local-only failures).
3. PR-event `images.yml` / `helpers.yml` runs **build images but do not push** — verified by checking `ghcr.io/bolin8017` packages: no PR-tagged versions exist.
4. After merging to `main`, `images.yml` and `helpers.yml` push to GHCR with tags `main`, `main-<sha>`, `sha-<long-sha>`.
5. Pushing a throwaway tag `v0.0.0-test` triggers `images.yml` / `helpers.yml` to push semver tags `0.0.0-test`, `0.0`, `0`, `latest`. (Then delete the tag.)
6. A docs-only PR (e.g. typo in `docs/architecture.md`) triggers **only `lint.yml`** — other workflows skip via path filter.
7. A change to `charts/lolday/helpers/mlflow-server/Dockerfile` does **not** trigger `helpers.yml` (path filter excludes it by design).
8. Dependabot's first run produces **at least one PR** for each of the four ecosystems (`github-actions`, `pip`, `npm`, `docker`).
9. `docs/architecture.md` §6 + §9 #2 reflect the resolved state with this PR's URL.
10. Six README badges show green on `main`.
11. `.claude/rules/github-actions.md` exists; `.claude/rules/{backend,frontend,scripts-and-ops,charts-and-helm}.md` each carry the cross-reference line.
12. Operator (per `docs/conventions.md` runbook) configures branch protection in GitHub UI after the PR merges. Spec acknowledges this as a manual step.

## Open questions

None. All design decisions resolved during 2026-04-29 brainstorming (Q1–Q5).
