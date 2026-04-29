# GitHub Actions CI/CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land 6 GitHub Actions workflows + 3 composite actions + Dependabot + documentation that enforce hygiene/tests on every PR and publish container images to GHCR — closing `docs/architecture.md` §9 #2 (largest tech debt).

**Architecture:** Six flat workflows (no cross-workflow `needs:`), pre-commit as the single source of truth for lint/format, GHCR as the only registry CI touches (Harbor stays manual on server30). All third-party actions pinned to commit SHA; Dependabot keeps them current.

**Tech Stack:** GitHub Actions (yaml), composite actions, `astral-sh/setup-uv`, `actions/setup-node` + corepack pnpm, `azure/setup-helm`, `docker/build-push-action` with GHA cache backend, GHCR (`ghcr.io/bolin8017/lolday-*`).

**Spec:** `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md` — read it before starting.

---

## Reference: pinned action SHAs

These are the canonical SHA pins used throughout this plan. Resolved from upstream tags via `gh api repos/<owner>/<repo>/git/refs/tags/<tag>` on 2026-04-30. **Use these exact strings — do not re-resolve unless Dependabot has already opened a bump PR for that action.**

```
actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683          # v4.2.2
actions/cache@1bd1e32a3bdc45362d1e726936510720a7c30a57             # v4.2.0
actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020         # v4.4.0
actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02    # v4.6.2
astral-sh/setup-uv@0c5e2b8115b80b4c7c5ddf6ffdd634974642d182         # v5.4.1
azure/setup-helm@b9e51907a09c216f16ebe8536097933489208112           # v4.3.0
docker/setup-buildx-action@b5ca514318bd6ebac0fb2aedd5d36ec1b5c232a2 # v3.10.0
docker/login-action@74a5d142397b4f367a81961eba4e8cd7edddf772        # v3.4.0
docker/metadata-action@902fa8ec7d6ecbf8d84d538b9b233a880e428804     # v5.7.0
docker/build-push-action@263435318d21b8e681c14492fe198d362a7d2c83   # v6.18.0
```

---

## File map

**New files:**

- `.github/actions/setup-uv/action.yml`
- `.github/actions/setup-pnpm-node/action.yml`
- `.github/actions/docker-meta-build/action.yml`
- `.github/workflows/lint.yml`
- `.github/workflows/backend.yml`
- `.github/workflows/frontend.yml`
- `.github/workflows/helm.yml`
- `.github/workflows/images.yml`
- `.github/workflows/helpers.yml`
- `.github/dependabot.yml`
- `.claude/rules/github-actions.md`
- `frontend/.nvmrc`

**Modified files:**

- `.gitignore` (append act cache pattern)
- `README.md` (add badge bar)
- `CLAUDE.md` (quickstart line)
- `docs/conventions.md` (new §10 CI/CD)
- `docs/architecture.md` (§6 rewrite, §9 #2 mark resolved)
- `docs/runbooks/release-helpers.md` (rewrite §«CI integration sketch»)
- `.claude/rules/backend.md` (cross-link)
- `.claude/rules/frontend.md` (cross-link)
- `.claude/rules/scripts-and-ops.md` (cross-link)
- `.claude/rules/charts-and-helm.md` (cross-link)

---

## Execution order

```
Wave 0 (sequential)
└── Task 1: branch + frontend/.nvmrc + .gitignore

Wave 1 (parallel — composite actions; no inter-dependency)
├── Task 2: setup-uv composite
├── Task 3: setup-pnpm-node composite
└── Task 4: docker-meta-build composite

Wave 2 (parallel — workflows; depends on Wave 1)
├── Task 5: lint.yml
├── Task 6: backend.yml
├── Task 7: frontend.yml
├── Task 8: helm.yml
├── Task 9: images.yml
└── Task 10: helpers.yml

Wave 3 (parallel — dependabot + docs/rules; no dependency)
├── Task 11: dependabot.yml
├── Task 12: .claude/rules/github-actions.md
├── Task 13: cross-link .claude/rules/{backend,frontend,scripts-and-ops,charts-and-helm}.md
├── Task 14: README badges
├── Task 15: docs/conventions.md §10
├── Task 16: docs/architecture.md §6 + §9 #2
├── Task 17: CLAUDE.md quickstart
└── Task 18: docs/runbooks/release-helpers.md rewrite

Wave 4 (sequential)
├── Task 19: local pre-commit pass + push + open PR + iterate to 6 green
├── Task 20: path-filter / tag-trigger acceptance verification
└── Task 21: branch protection setup runbook (operator manual)
```

---

## Task 1: Branch + frontend/.nvmrc + .gitignore

**Files:**

- Create: `frontend/.nvmrc`
- Modify: `.gitignore` (append)

- [ ] **Step 1: Create feature branch**

```bash
git checkout main
git pull --ff-only
git checkout -b feat/github-actions-cicd
```

- [ ] **Step 2: Create `frontend/.nvmrc`**

Content (single line, no trailing whitespace beyond newline):

```
22
```

Use Write tool to create at `/home/bolin8017/Documents/repositories/lolday/frontend/.nvmrc` with content `22\n`.

Why: `actions/setup-node` reads `node-version-file: frontend/.nvmrc` in the `setup-pnpm-node` composite; matches `frontend/package.json` `"engines": { "node": ">=22" }` and `frontend/Dockerfile` `node:22-alpine`.

- [ ] **Step 3: Append act-cache pattern to `.gitignore`**

Append exactly these three lines at end of `/home/bolin8017/Documents/repositories/lolday/.gitignore` (do not duplicate any existing pattern):

```
# Local GHA debug artifacts (nektos/act)
/.github/.cache/
```

- [ ] **Step 4: Verify pre-commit green on the two changes**

```bash
pre-commit run --files frontend/.nvmrc .gitignore
```

Expected: every reported hook either Passed or Skipped (no Failed).

- [ ] **Step 5: Commit**

```bash
git add frontend/.nvmrc .gitignore
git commit -m "$(cat <<'EOF'
chore(ci): pin Node 22 via frontend/.nvmrc; ignore act cache

Pre-flight for the GitHub Actions phase. setup-node will read this file in the setup-pnpm-node composite action.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Composite action — setup-uv

**Files:**

- Create: `.github/actions/setup-uv/action.yml`

- [ ] **Step 1: Write the composite action**

Create `/home/bolin8017/Documents/repositories/lolday/.github/actions/setup-uv/action.yml`:

```yaml
name: setup-uv
description: Install uv and run `uv sync --frozen` against a project directory.
inputs:
  working-directory:
    description: Project directory passed to `uv sync --project`. Default `backend`.
    required: false
    default: backend
runs:
  using: composite
  steps:
    - name: Install uv (cached)
      uses: astral-sh/setup-uv@0c5e2b8115b80b4c7c5ddf6ffdd634974642d182 # v5.4.1
      with:
        enable-cache: true
        cache-dependency-glob: "**/uv.lock"
    - name: uv sync ${{ inputs.working-directory }}
      shell: bash
      run: uv sync --frozen --project ${{ inputs.working-directory }}
```

- [ ] **Step 2: Validate yaml + prettier**

```bash
pre-commit run --files .github/actions/setup-uv/action.yml
```

Expected: Passed (or Skipped) for every hook. If prettier reformats, re-run once.

- [ ] **Step 3: Commit**

```bash
git add .github/actions/setup-uv/action.yml
git commit -m "$(cat <<'EOF'
ci: add setup-uv composite action

Wraps astral-sh/setup-uv with cache + uv sync --frozen --project. Used by lint.yml and backend.yml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Composite action — setup-pnpm-node

**Files:**

- Create: `.github/actions/setup-pnpm-node/action.yml`

- [ ] **Step 1: Write the composite action**

Create `/home/bolin8017/Documents/repositories/lolday/.github/actions/setup-pnpm-node/action.yml`:

```yaml
name: setup-pnpm-node
description: Corepack-enable pnpm, run setup-node with pnpm cache, install frontend deps.
runs:
  using: composite
  steps:
    - name: Enable corepack
      shell: bash
      run: corepack enable
    - name: Setup Node from frontend/.nvmrc with pnpm cache
      uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
      with:
        node-version-file: frontend/.nvmrc
        cache: pnpm
        cache-dependency-path: frontend/pnpm-lock.yaml
    - name: pnpm install (frontend, frozen)
      shell: bash
      run: pnpm --dir frontend install --frozen-lockfile
```

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/actions/setup-pnpm-node/action.yml
```

Expected: every hook Passed/Skipped.

- [ ] **Step 3: Commit**

```bash
git add .github/actions/setup-pnpm-node/action.yml
git commit -m "$(cat <<'EOF'
ci: add setup-pnpm-node composite action

Corepack-enables pnpm, runs setup-node with built-in pnpm store cache, then `pnpm --dir frontend install --frozen-lockfile`. Used by lint.yml and frontend.yml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Composite action — docker-meta-build

**Files:**

- Create: `.github/actions/docker-meta-build/action.yml`

- [ ] **Step 1: Write the composite action**

Create `/home/bolin8017/Documents/repositories/lolday/.github/actions/docker-meta-build/action.yml`:

```yaml
name: docker-meta-build
description: Build (and optionally push) a Dockerfile to GHCR with mainstream tag rules.
inputs:
  image:
    description: Image short name (final segment after ghcr.io/bolin8017/, e.g. `lolday-backend`).
    required: true
  context:
    description: Build context path (relative to repo root).
    required: true
  push:
    description: '"true" to push, "false" to build only. Caller passes ${{ github.event_name != ''pull_request'' }}.'
    required: true
runs:
  using: composite
  steps:
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@b5ca514318bd6ebac0fb2aedd5d36ec1b5c232a2 # v3.10.0
    - name: Login to GHCR
      if: inputs.push == 'true'
      uses: docker/login-action@74a5d142397b4f367a81961eba4e8cd7edddf772 # v3.4.0
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ github.token }}
    - name: Compute image metadata
      id: meta
      uses: docker/metadata-action@902fa8ec7d6ecbf8d84d538b9b233a880e428804 # v5.7.0
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
    - name: Build and push
      uses: docker/build-push-action@263435318d21b8e681c14492fe198d362a7d2c83 # v6.18.0
      with:
        context: ${{ inputs.context }}
        push: ${{ inputs.push }}
        tags: ${{ steps.meta.outputs.tags }}
        labels: ${{ steps.meta.outputs.labels }}
        cache-from: type=gha,scope=${{ inputs.image }}
        cache-to: type=gha,scope=${{ inputs.image }},mode=max
```

Note on `${{ github.token }}`: composite actions cannot read `env.GITHUB_TOKEN` from the caller; `github.token` is the canonical token reference inside composites and works without explicit input.

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/actions/docker-meta-build/action.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/actions/docker-meta-build/action.yml
git commit -m "$(cat <<'EOF'
ci: add docker-meta-build composite action

Wraps buildx setup, conditional GHCR login (skipped on PR), metadata-action with mainstream tag rules, and build-push-action with per-image GHA cache scope. Used by images.yml and helpers.yml.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Workflow — lint.yml

**Files:**

- Create: `.github/workflows/lint.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/lint.yml`:

```yaml
name: lint

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  pre-commit:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout (full history for helpers-lock-fresh)
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0

      - name: Setup uv + sync backend (mypy hook prerequisite)
        uses: ./.github/actions/setup-uv

      - name: Setup pnpm + install frontend (prettier/eslint hook prerequisite)
        uses: ./.github/actions/setup-pnpm-node

      - name: Cache pre-commit hook envs
        uses: actions/cache@1bd1e32a3bdc45362d1e726936510720a7c30a57 # v4.2.0
        with:
          path: ~/.cache/pre-commit
          key: ${{ runner.os }}-precommit-${{ hashFiles('.pre-commit-config.yaml') }}

      - name: Install pre-commit
        run: uv tool install pre-commit

      - name: Run pre-commit on all files
        run: pre-commit run --all-files --show-diff-on-failure --color always
```

Key invariants documented in spec §4.1:

- `fetch-depth: 0` is mandatory — `helpers-lock-fresh` calls `scripts/check-helpers-lock.sh` which uses `git rev-parse HEAD:<path>`; shallow fails.
- `setup-uv` and `setup-pnpm-node` MUST run before `pre-commit run` because mypy/prettier/eslint are `language: system` hooks that need `backend/.venv` and `frontend/node_modules`.

- [ ] **Step 2: Validate yaml + prettier**

```bash
pre-commit run --files .github/workflows/lint.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/lint.yml
git commit -m "$(cat <<'EOF'
ci: add lint.yml — pre-commit run --all-files as single source of truth

Runs the same .pre-commit-config.yaml operators run locally. fetch-depth: 0 because helpers-lock-fresh hook needs full history. uv tool install pre-commit + actions/cache for ~/.cache/pre-commit cuts warm-cache run to <10s.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Workflow — backend.yml

**Files:**

- Create: `.github/workflows/backend.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/backend.yml`:

```yaml
name: backend

on:
  push:
    branches: [main]
    paths-ignore:
      - "**.md"
      - "docs/**"
      - "frontend/**"
      - "charts/**"
      - "scripts/**"
  pull_request:
    branches: [main]
    paths-ignore:
      - "**.md"
      - "docs/**"
      - "frontend/**"
      - "charts/**"
      - "scripts/**"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  pytest:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Setup uv + sync backend (with dev group)
        uses: ./.github/actions/setup-uv

      - name: Run pytest
        working-directory: backend
        run: uv run pytest -v --tb=short
```

Why no Postgres/Redis service container: `backend/tests/conftest.py` uses aiosqlite; MLflow autouse-mocked; fakeredis covers Redis. Adding Postgres would mask the documented design (`docs/architecture.md` §6, `.claude/rules/backend.md`).

Why no separate ruff/mypy step: covered by `lint.yml` (single source of truth, spec §3.2).

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/workflows/backend.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/backend.yml
git commit -m "$(cat <<'EOF'
ci: add backend.yml — uv run pytest with aiosqlite

Runs only pytest; ruff/mypy are owned by lint.yml. paths-ignore skips docs / frontend / charts / scripts changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Workflow — frontend.yml

**Files:**

- Create: `.github/workflows/frontend.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/frontend.yml`:

```yaml
name: frontend

on:
  push:
    branches: [main]
    paths-ignore:
      - "**.md"
      - "docs/**"
      - "backend/**"
      - "charts/**"
      - "scripts/**"
  pull_request:
    branches: [main]
    paths-ignore:
      - "**.md"
      - "docs/**"
      - "backend/**"
      - "charts/**"
      - "scripts/**"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  unit:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Setup pnpm + install frontend
        uses: ./.github/actions/setup-pnpm-node

      - name: Typecheck
        run: pnpm --dir frontend typecheck

      - name: Vitest
        run: pnpm --dir frontend test

  # ---------------------------------------------------------------------------
  # FUTURE PHASE: Playwright E2E. Spec §4.3 deferred (no Postgres/MLflow/CF
  # Access mocking infrastructure in CI yet). Spec doc:
  #   docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md
  # When activating, this needs at minimum:
  #   - Postgres + Redis service containers OR aiosqlite-only backend mode
  #   - MLflow mock or skip-mode
  #   - CF Access bypass for test JWT
  # Until then, operator runs `pnpm --dir frontend playwright test` locally
  # before merging frontend changes.
  # ---------------------------------------------------------------------------
  # playwright-e2e:
  #   runs-on: ubuntu-24.04
  #   steps:
  #     - uses: actions/checkout@... # pin SHA
  #     - uses: ./.github/actions/setup-pnpm-node
  #     - run: pnpm --dir frontend playwright install --with-deps
  #     - run: pnpm --dir frontend playwright test
```

Spec §4.3 explicitly requires the playwright-e2e block to remain commented-out as a future-phase hook.

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/workflows/frontend.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/frontend.yml
git commit -m "$(cat <<'EOF'
ci: add frontend.yml — vitest + typecheck

Single unit job: tsc --noEmit + vitest. paths-ignore skips backend / charts / scripts / docs. Playwright E2E intentionally left as commented-out future-phase hook.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Workflow — helm.yml

**Files:**

- Create: `.github/workflows/helm.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/helm.yml`:

```yaml
name: helm

on:
  push:
    branches: [main]
    paths:
      - "charts/**"
      - ".github/workflows/helm.yml"
  pull_request:
    branches: [main]
    paths:
      - "charts/**"
      - ".github/workflows/helm.yml"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  lint-template:
    runs-on: ubuntu-24.04
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Setup helm
        uses: azure/setup-helm@b9e51907a09c216f16ebe8536097933489208112 # v4.3.0
        with:
          version: v3.16.4

      - name: Cache sub-chart tgz
        uses: actions/cache@1bd1e32a3bdc45362d1e726936510720a7c30a57 # v4.2.0
        with:
          path: charts/lolday/charts
          key: ${{ runner.os }}-helm-deps-${{ hashFiles('charts/lolday/Chart.lock') }}

      - name: helm dependency update
        run: helm dependency update charts/lolday

      - name: helm lint
        run: helm lint charts/lolday

      - name: helm template (render-only sanity)
        run: helm template lolday charts/lolday --namespace lolday > /tmp/manifests.yaml

      - name: Upload rendered manifests
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        with:
          name: helm-manifests-${{ github.run_id }}
          path: /tmp/manifests.yaml
          retention-days: 14
```

Helm 3.16.4 is current stable as of 2026-04 and matches what `scripts/install-tools.sh` installs locally.

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/workflows/helm.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/helm.yml
git commit -m "$(cat <<'EOF'
ci: add helm.yml — dependency update + lint + template render

Path-filtered to charts/**. Caches sub-chart tgz on Chart.lock hash. Uploads rendered manifests as 14-day artefact for reviewer diff.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Workflow — images.yml

**Files:**

- Create: `.github/workflows/images.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/images.yml`:

```yaml
name: images

on:
  push:
    branches: [main]
    tags:
      - "v*.*.*"
    paths:
      - "backend/**"
      - "frontend/**"
      - ".github/workflows/images.yml"
      - ".github/actions/docker-meta-build/**"
  pull_request:
    branches: [main]
    paths:
      - "backend/**"
      - "frontend/**"
      - ".github/workflows/images.yml"
      - ".github/actions/docker-meta-build/**"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event_name }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  build-image:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      packages: write
    strategy:
      fail-fast: false
      matrix:
        image: [backend, frontend]
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Build (and push if not PR)
        uses: ./.github/actions/docker-meta-build
        with:
          image: lolday-${{ matrix.image }}
          context: ./${{ matrix.image }}
          push: ${{ github.event_name != 'pull_request' }}
```

Note: tag-push triggers ignore the `paths` filter on the tag itself (GitHub does not apply path filters to tag refs by default — every tag push runs every workflow that lists the tag pattern). This is what we want for releases. PRs and main pushes still respect path filters.

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/workflows/images.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/images.yml
git commit -m "$(cat <<'EOF'
ci: add images.yml — backend/frontend build + GHCR push

Matrix builds both backend and frontend Dockerfiles. PR builds verify only (no push). main push tags as main / main-<sha> / sha-<long>. Tag push (v*.*.*) tags semver + latest.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Workflow — helpers.yml

**Files:**

- Create: `.github/workflows/helpers.yml`

- [ ] **Step 1: Write the workflow**

Create `/home/bolin8017/Documents/repositories/lolday/.github/workflows/helpers.yml`:

```yaml
name: helpers

on:
  push:
    branches: [main]
    paths:
      - "charts/lolday/helpers/build-helper/**"
      - "charts/lolday/helpers/job-helper/**"
      - ".github/workflows/helpers.yml"
      - ".github/actions/docker-meta-build/**"
  pull_request:
    branches: [main]
    paths:
      - "charts/lolday/helpers/build-helper/**"
      - "charts/lolday/helpers/job-helper/**"
      - ".github/workflows/helpers.yml"
      - ".github/actions/docker-meta-build/**"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}-${{ github.event_name }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  build-helper:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      packages: write
    strategy:
      fail-fast: false
      matrix:
        helper: [build-helper, job-helper]
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Build (and push if not PR)
        uses: ./.github/actions/docker-meta-build
        with:
          image: lolday-${{ matrix.helper }}
          context: ./charts/lolday/helpers/${{ matrix.helper }}
          push: ${{ github.event_name != 'pull_request' }}
```

Spec §4.6: `mlflow-server` and `pytorch-cu12-base` deliberately excluded from both `paths` filter and `matrix` (B3 decision — external base images, infrequent updates, large body, operator manual).

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/workflows/helpers.yml
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/helpers.yml
git commit -m "$(cat <<'EOF'
ci: add helpers.yml — build/job-helper image build + GHCR push

Matrix on build-helper / job-helper. Path-filtered: only the two helper subtrees + the workflow itself + the docker-meta-build composite. mlflow-server / pytorch-cu12-base excluded by design (operator manual).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Dependabot

**Files:**

- Create: `.github/dependabot.yml`

- [ ] **Step 1: Write the config**

Create `/home/bolin8017/Documents/repositories/lolday/.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 10
    groups:
      actions-minor-patch:
        patterns: ["*"]
        update-types:
          - minor
          - patch

  - package-ecosystem: pip
    directory: /backend
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 10
    groups:
      backend-minor-patch:
        patterns: ["*"]
        update-types:
          - minor
          - patch

  - package-ecosystem: npm
    directory: /frontend
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 10
    groups:
      frontend-minor-patch:
        patterns: ["*"]
        update-types:
          - minor
          - patch

  - package-ecosystem: docker
    directory: /backend
    schedule:
      interval: weekly

  - package-ecosystem: docker
    directory: /frontend
    schedule:
      interval: weekly

  - package-ecosystem: docker
    directory: /charts/lolday/helpers/build-helper
    schedule:
      interval: weekly

  - package-ecosystem: docker
    directory: /charts/lolday/helpers/job-helper
    schedule:
      interval: weekly

  - package-ecosystem: docker
    directory: /charts/lolday/helpers/mlflow-server
    schedule:
      interval: weekly

  - package-ecosystem: docker
    directory: /charts/lolday/helpers/pytorch-cu12-base
    schedule:
      interval: weekly
```

`mlflow-server` and `pytorch-cu12-base` ARE tracked by Dependabot (Dockerfile FROM bumps need review) even though their CI build is out of scope. Operator merges the PR locally and rebuilds via `bash scripts/build-helpers.sh` (or its mlflow/pytorch equivalents).

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .github/dependabot.yml
```

Note: Dependabot's own schema validation only happens on GitHub side after push; pre-commit only checks generic yaml.

- [ ] **Step 3: Commit**

```bash
git add .github/dependabot.yml
git commit -m "$(cat <<'EOF'
ci: add dependabot.yml — github-actions + pip + npm + docker

Weekly cadence Mondays. Minor/patch grouped per ecosystem to reduce PR noise. Helper Dockerfiles all tracked (including mlflow-server / pytorch-cu12-base) so base-image bumps still get reviewer attention.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `.claude/rules/github-actions.md`

**Files:**

- Create: `.claude/rules/github-actions.md`

- [ ] **Step 1: Write the rule file**

Create `/home/bolin8017/Documents/repositories/lolday/.claude/rules/github-actions.md`:

````markdown
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

## Two-registry model

- `ghcr.io/bolin8017/lolday-*` — CI artifact registry. PR builds verify; `main` and tag pushes publish.
- `harbor.lolday.svc:80/lolday/*` — production runtime registry, server30-internal. Populated by operator running `bash scripts/build-helpers.sh` (and parallel manual flows for backend / frontend / mlflow-server / pytorch-cu12-base).

CI never pushes to Harbor. Spec rationale: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md` §3.1.

## Adding a new image

Add a matrix entry to the appropriate workflow:

- backend / frontend / new platform image → `images.yml` `matrix.image`.
- helper-class image → `helpers.yml` `matrix.helper`. **`mlflow-server` and `pytorch-cu12-base` are out of scope** (external base images, low-frequency updates, large body — operator manual). Adding them requires updating the spec first.

Do not create a new workflow file per image — the matrix pattern is the mainstream way.

## Adding a new ecosystem to Dependabot

Edit `.github/dependabot.yml`. Do not bypass with hand-edits to lockfiles.

## Composite actions

Three composites under `.github/actions/`:

- `setup-uv` — wraps `astral-sh/setup-uv` + `uv sync --frozen --project <dir>`.
- `setup-pnpm-node` — corepack + setup-node (with pnpm cache) + `pnpm --dir frontend install --frozen-lockfile`.
- `docker-meta-build` — buildx + conditional GHCR login + metadata-action + build-push-action with per-image GHA cache scope.

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
````

- [ ] **Step 2: Validate**

```bash
pre-commit run --files .claude/rules/github-actions.md
```

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/github-actions.md
git commit -m "$(cat <<'EOF'
docs(rules): add path-scoped CLAUDE rule for .github/

Captures: SHA pinning, least-privilege permissions, ban on pull_request_target, single-source-of-truth lint, two-registry model, composite-action conventions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Cross-link `.claude/rules/{backend,frontend,scripts-and-ops,charts-and-helm}.md`

**Files:**

- Modify: `.claude/rules/backend.md`
- Modify: `.claude/rules/frontend.md`
- Modify: `.claude/rules/scripts-and-ops.md`
- Modify: `.claude/rules/charts-and-helm.md`

- [ ] **Step 1: Append cross-link to `.claude/rules/backend.md`**

Use Read tool first to confirm current EOF (if you skipped, this Edit will fail per harness rules).

Append to `/home/bolin8017/Documents/repositories/lolday/.claude/rules/backend.md`:

```markdown
## CI

Enforced by `.github/workflows/{lint,backend}.yml`. Discipline rules in `.claude/rules/github-actions.md`. Do not duplicate ruff / mypy invocations in `backend.yml` — `lint.yml` owns hygiene.
```

- [ ] **Step 2: Append cross-link to `.claude/rules/frontend.md`**

Append:

```markdown
## CI

Enforced by `.github/workflows/{lint,frontend}.yml`. Discipline rules in `.claude/rules/github-actions.md`. Do not duplicate prettier / eslint / typecheck invocations in `frontend.yml` — `lint.yml` owns hygiene.
```

- [ ] **Step 3: Append cross-link to `.claude/rules/scripts-and-ops.md`**

Append:

```markdown
## CI

Engineering-hygiene scripts (pre-commit, install-tools.sh) are mirrored on every PR by `.github/workflows/lint.yml`. Discipline rules in `.claude/rules/github-actions.md`.
```

- [ ] **Step 4: Append cross-link to `.claude/rules/charts-and-helm.md`**

Append:

```markdown
## CI

`helm dependency update`, `helm lint`, `helm template` enforced by `.github/workflows/helm.yml`. Helper image Dockerfile build verification (build-helper, job-helper only) by `.github/workflows/helpers.yml` — `mlflow-server` and `pytorch-cu12-base` are excluded by design (operator manual). Discipline rules in `.claude/rules/github-actions.md`.
```

- [ ] **Step 5: Validate**

```bash
pre-commit run --files .claude/rules/backend.md .claude/rules/frontend.md .claude/rules/scripts-and-ops.md .claude/rules/charts-and-helm.md
```

- [ ] **Step 6: Commit**

```bash
git add .claude/rules/backend.md .claude/rules/frontend.md .claude/rules/scripts-and-ops.md .claude/rules/charts-and-helm.md
git commit -m "$(cat <<'EOF'
docs(rules): cross-link area rules to .claude/rules/github-actions.md

Each path-scoped rule now points at the new GitHub Actions rule. Reinforces single-source-of-truth: do not duplicate lint invocations between lint.yml and area workflows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: README badge bar

**Files:**

- Modify: `README.md` (insert under `# Lolday` line)

- [ ] **Step 1: Read current top of `README.md`**

Use Read tool on `/home/bolin8017/Documents/repositories/lolday/README.md`. Confirm lines 1–3 are:

```
# Lolday

Internal ML platform for ISLab malware detector management.
```

- [ ] **Step 2: Insert badge bar after `# Lolday` line**

Use Edit tool with this `old_string`:

```
# Lolday

Internal ML platform for ISLab malware detector management.
```

And this `new_string`:

```
# Lolday

[![lint](https://github.com/bolin8017/lolday/actions/workflows/lint.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/lint.yml)
[![backend](https://github.com/bolin8017/lolday/actions/workflows/backend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/backend.yml)
[![frontend](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/frontend.yml)
[![helm](https://github.com/bolin8017/lolday/actions/workflows/helm.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helm.yml)
[![images](https://github.com/bolin8017/lolday/actions/workflows/images.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/images.yml)
[![helpers](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml/badge.svg)](https://github.com/bolin8017/lolday/actions/workflows/helpers.yml)

Internal ML platform for ISLab malware detector management.
```

- [ ] **Step 3: Validate**

```bash
pre-commit run --files README.md
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): add CI status badge bar

Six badges: lint / backend / frontend / helm / images / helpers. Private repo, badges still render; click-through requires login.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `docs/conventions.md` §10 CI/CD

**Files:**

- Modify: `docs/conventions.md` (append §10)

- [ ] **Step 1: Read current EOF of `docs/conventions.md`**

Use Read tool. Confirm the file ends with §9 «Before writing new code» and the last bullet `backend/migrations/... → .claude/rules/alembic-migrations.md`.

- [ ] **Step 2: Append §10 by editing the last bullet of §9**

Use Edit tool with `old_string`:

```
- `backend/migrations/...` → `.claude/rules/alembic-migrations.md`
```

And `new_string` (notice we keep the §9 bullet, then add a blank line + §10 in full):

````
- `backend/migrations/...` → `.claude/rules/alembic-migrations.md`

## 10. CI / CD (GitHub Actions)

> Source spec: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md`. Detailed discipline lives in `.claude/rules/github-actions.md`.

### 10.1 Workflow inventory

| Workflow              | Triggers                                                                | What it does                                                                              |
| --------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `lint.yml`            | every push to `main`, every PR                                          | `pre-commit run --all-files` (single source of truth for ruff / mypy / prettier / eslint) |
| `backend.yml`         | path-filtered to `backend/**`                                           | `cd backend && uv run pytest`                                                             |
| `frontend.yml`        | path-filtered to `frontend/**`                                          | `pnpm typecheck` + `pnpm test` (vitest); playwright deferred                              |
| `helm.yml`            | path-filtered to `charts/**`                                            | `helm dep update` + `helm lint` + `helm template`                                         |
| `images.yml`          | `backend/Dockerfile` / `frontend/Dockerfile` paths + tag `v*.*.*`       | matrix build backend / frontend → GHCR (PR builds only, no push)                          |
| `helpers.yml`         | path-filtered to `charts/lolday/helpers/{build,job}-helper/**`          | matrix build → GHCR; `mlflow-server` and `pytorch-cu12-base` excluded by design           |

### 10.2 Pre-commit is the single source of truth

The CI's `lint.yml` runs `pre-commit run --all-files` against the same `.pre-commit-config.yaml` operators run locally. Do **not** add parallel `uv run ruff check` / `pnpm lint` steps to other workflows — duplication breeds drift. To add a new check, edit `.pre-commit-config.yaml`; CI follows automatically.

### 10.3 Two-registry model

- **GHCR** — `ghcr.io/bolin8017/lolday-{backend,frontend,build-helper,job-helper}`. Populated by CI on `main` and tag pushes. Verification artefact.
- **Harbor** — `harbor.lolday.svc:80/lolday/*`. Production runtime registry, server30-internal. Populated by operator running `bash scripts/build-helpers.sh` and the parallel manual flows for backend / frontend / mlflow-server / pytorch-cu12-base. CI never pushes here.

Why split: Harbor is unreachable from GitHub-hosted runners by design (`docs/architecture.md` §5.3); see spec §3.1 for the rejection of self-hosted-runner / Cloudflare-Tunnel-Harbor alternatives.

### 10.4 Image tag rules

| Trigger             | Tags applied                                       |
| ------------------- | -------------------------------------------------- |
| `push: main`        | `main`, `main-<short-sha>`, `sha-<long-sha>`       |
| `push: tag v1.2.3`  | `1.2.3`, `1.2`, `1`, `latest`                      |
| `pull_request`      | not pushed (build only)                            |

### 10.5 Releasing

```bash
git tag v0.1.0
git push --tags
```

`images.yml` and `helpers.yml` (NB: helpers does **not** trigger on tag — its tag is content-addressable subtree SHA via `helpers.lock`, not platform semver) push semver tags to GHCR. Production deploy on server30 is unaffected — operator continues `bash scripts/deploy.sh`.

### 10.6 Branch-protection setup (operator manual)

GitHub provides no in-repo declarative branch-protection API stable enough to depend on. After the CI PR merges, operator goes to `Settings → Branches → Add branch ruleset` (or classic «Add rule» on `main`) and configures:

1. **Require a pull request before merging** — yes.
2. **Required status checks** — add all six:
   - `lint / pre-commit`
   - `backend / pytest`
   - `frontend / unit`
   - `helm / lint-template`
   - `images / build-image (backend)`, `images / build-image (frontend)`
   - `helpers / build-helper (build-helper)`, `helpers / build-helper (job-helper)`
3. **Require branches to be up to date before merging** — yes.
4. **Require conversation resolution** — yes.
5. **Require linear history** — yes.
6. **Restrict who can push to matching branches / disallow force pushes** — yes.
7. **Allow squash merge only** (Settings → General → Pull Requests).

### 10.7 Dependabot SOP

Weekly Mondays. Per ecosystem:

- **`github-actions`** — bumps SHA pin + comment in lock-step. Green CI = squash merge.
- **`pip`** (backend) — minor/patch grouped; verify `cd backend && uv lock` aligns with the merged `pyproject.toml`.
- **`npm`** (frontend) — minor/patch grouped; check peer-dep warnings in CI log.
- **`docker`** (per Dockerfile dir) — base-image bump. For `mlflow-server` and `pytorch-cu12-base`, merging the PR is half the work — operator must rebuild manually because their CI build is out of scope.
````

- [ ] **Step 3: Validate**

```bash
pre-commit run --files docs/conventions.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/conventions.md
git commit -m "$(cat <<'EOF'
docs(conventions): add §10 CI/CD

Workflow inventory, pre-commit single-source-of-truth, two-registry model, image tag rules, release flow, branch-protection runbook, Dependabot SOP.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: `docs/architecture.md` §6 + §9 #2 update

**Files:**

- Modify: `docs/architecture.md` (§6 first paragraph, §9 #2 entry)

- [ ] **Step 1: Read both target sections**

Use Read tool on `/home/bolin8017/Documents/repositories/lolday/docs/architecture.md`. Confirm:

- §6 starts at line 199 with `## 6. Build / Test / Release` and immediately has subsection `### No CI/CD` at line 201.
- §9 #2 is at line 288: `2. **No CI/CD.** No GitHub Actions, no automated build/test, no release pipeline. \`scripts/deploy.sh\` is manual.`

- [ ] **Step 2: Rewrite §6 first subsection**

Use Edit tool with `old_string`:

```
### No CI/CD

There is no `.github/workflows/`. No automated build, test, lint, or release pipeline. All build and release happens locally then via `bash scripts/deploy.sh`. This is tech debt (see §9).
```

And `new_string`:

```
### CI/CD overview

Six GitHub Actions workflows under `.github/workflows/` enforce hygiene + tests on every PR and publish container images to GHCR on `main` / tag pushes:

- `lint.yml` — `pre-commit run --all-files` (single source of truth).
- `backend.yml` — `cd backend && uv run pytest`.
- `frontend.yml` — `pnpm typecheck` + `pnpm test` (vitest). Playwright deferred (commented-out hook).
- `helm.yml` — `helm dependency update` + `helm lint` + `helm template`.
- `images.yml` — backend / frontend Dockerfile build → GHCR.
- `helpers.yml` — build-helper / job-helper Dockerfile build → GHCR (mlflow-server / pytorch-cu12-base out of scope).

CI is **verification + GHCR artefact only**. Production registry (`harbor.lolday.svc:80/lolday/*`) and `bash scripts/deploy.sh` remain operator-driven on server30. See `docs/conventions.md` §10 and `.claude/rules/github-actions.md`.
```

- [ ] **Step 3: Update §9 #2 to mark resolved**

Use Edit tool with `old_string`:

```
2. **No CI/CD.** No GitHub Actions, no automated build/test, no release pipeline. `scripts/deploy.sh` is manual.
```

And `new_string`:

```
2. ~~**No CI/CD.**~~ — resolved 2026-04-30 in `feat/github-actions-cicd`. Six GitHub Actions workflows under `.github/workflows/` enforce lint / tests / image build on every PR; GHCR receives `main` / tag pushes. Production deploy (`scripts/deploy.sh`) remains operator-driven by design. Spec: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md`. Discipline rules: `.claude/rules/github-actions.md`. Conventions: `docs/conventions.md` §10.
```

- [ ] **Step 4: Validate**

```bash
pre-commit run --files docs/architecture.md
```

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs(architecture): mark CI/CD tech debt as resolved (§6 + §9 #2)

§6 «No CI/CD» rewritten into «CI/CD overview». §9 #2 strikethrough + resolution note pointing at the 2026-04-30 spec / rules / conventions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: `CLAUDE.md` quickstart line

**Files:**

- Modify: `CLAUDE.md` (insert under quickstart `pre-commit run --all-files` line)

- [ ] **Step 1: Read the quickstart block**

Use Read tool on `/home/bolin8017/Documents/repositories/lolday/CLAUDE.md`. Confirm line 69 is:

```
pre-commit run --all-files              # lint+format whole repo (also auto-runs on git commit)
```

- [ ] **Step 2: Append `gh workflow run` line below it**

Use Edit tool with `old_string`:

```
pre-commit run --all-files              # lint+format whole repo (also auto-runs on git commit)
```

And `new_string`:

```
pre-commit run --all-files              # lint+format whole repo (also auto-runs on git commit)
gh workflow run lint.yml                # trigger CI sanity from local (needs gh CLI)
```

- [ ] **Step 3: Validate**

```bash
pre-commit run --files CLAUDE.md
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude-md): add gh workflow run lint.yml to quickstart

One-liner so contributors can sanity-check the lint workflow without pushing a branch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: Rewrite `docs/runbooks/release-helpers.md` §«CI integration sketch»

**Files:**

- Modify: `docs/runbooks/release-helpers.md` (replace §«CI integration sketch» block at lines ~86–109)

- [ ] **Step 1: Read the existing «CI integration sketch» block**

Use Read tool on `/home/bolin8017/Documents/repositories/lolday/docs/runbooks/release-helpers.md`. Confirm the block reads:

````
## CI integration sketch

Not implemented in this phase. The build script is pure-functional:

- Input: working tree git state.
- Output: Harbor pushes (idempotent against existing SHAs) + `helpers.lock` rewrite.

A future GitHub Actions / CI workflow can wrap it as:

```yaml
- run: bash scripts/build-helpers.sh
- run: |
    if ! git diff --exit-code charts/lolday/helpers.lock; then
      gh pr edit "$PR_NUMBER" --body "$(cat <<EOF
    Helper images rebuilt; lock updated. Reviewer: confirm and merge.
    EOF
    )"
      git config user.email "ci@lolday"
      git config user.name "lolday-ci"
      git add charts/lolday/helpers.lock
      git commit -m "chore(helpers): auto-rebuild helper image refs"
      git push
    fi
```
````

- [ ] **Step 2: Replace the entire block**

Use Edit tool with `old_string` set to the entire block from `## CI integration sketch` through the closing triple-backtick of the inner yaml block, and `new_string`:

```
## CI integration

`scripts/build-helpers.sh` is and remains the only sanctioned path that pushes helper images to **Harbor** (`harbor.lolday.svc:80`) and rewrites `charts/lolday/helpers.lock`. Operator runs it on server30 (or any host with reach to Harbor); commits the lock; deploys via `scripts/deploy.sh`.

CI does NOT call `build-helpers.sh`. Harbor is internal by design (see `docs/architecture.md` §5.3) and CI cannot reach it. What `.github/workflows/helpers.yml` does instead:

- On every PR that touches `charts/lolday/helpers/build-helper/**` or `charts/lolday/helpers/job-helper/**`: run `docker build` against the helper Dockerfile (no push) — verifies the image still builds cleanly.
- On `push: main` of those paths: same build, then push to **GHCR** (`ghcr.io/bolin8017/lolday-{build,job}-helper`) as a verification artefact and Dependabot-friendly mirror.

GHCR images are not used by production. They are a parallel CI artefact stream. A future server30-side cron mirroring GHCR → Harbor (e.g. `regctl image copy`) is a deferrable enhancement, not a CI dependency.

`mlflow-server` and `pytorch-cu12-base` are intentionally **outside** `helpers.yml`'s `paths` filter — their tags carry external semantic meaning, body sizes are large, and update frequency is low. Operator continues to build/push them manually when an upstream bump warrants it. Dependabot still tracks their Dockerfile FROM lines so the bump PR surfaces.
```

- [ ] **Step 3: Validate**

```bash
pre-commit run --files docs/runbooks/release-helpers.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/release-helpers.md
git commit -m "$(cat <<'EOF'
docs(runbooks): rewrite §CI integration to reflect actual GHCR-only model

The original «CI integration sketch» predated the CI design and assumed CI could push to Harbor. Replaced with the real model: helpers.yml verifies docker build + pushes to GHCR; Harbor stays operator-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: Local pre-commit pass + push branch + open PR + iterate to 6 green

**Files:** none modified.

- [ ] **Step 1: Run full pre-commit on the whole repo**

```bash
pre-commit run --all-files
```

Expected: every hook Passed or Skipped. If anything Fails, fix at root cause and re-run before pushing — `--no-verify` is forbidden by hard rule (`CLAUDE.md`).

- [ ] **Step 2: Verify branch state**

```bash
git status
git log --oneline main..HEAD
```

Expected: clean working tree; ~18 commits visible (one per Task 1–18).

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/github-actions-cicd
```

- [ ] **Step 4: Open PR**

```bash
gh pr create --title "feat(ci): GitHub Actions CI/CD — six workflows + GHCR + Dependabot" --body "$(cat <<'EOF'
## Summary

- Six GitHub Actions workflows under `.github/workflows/` (lint, backend, frontend, helm, images, helpers)
- Three composite actions under `.github/actions/` (setup-uv, setup-pnpm-node, docker-meta-build)
- `.github/dependabot.yml` covering github-actions / pip / npm / docker
- Documentation: README badges, `docs/conventions.md` §10 CI/CD, `docs/architecture.md` §6 + §9 #2 marked resolved, new `.claude/rules/github-actions.md`, cross-links from existing area rules

Resolves `docs/architecture.md` §9 #2 (largest tech debt — no CI).

Spec: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md`
Plan: `docs/superpowers/plans/2026-04-30-github-actions-cicd.md`

## Test plan

- [ ] All 6 workflows green on this PR
- [ ] PR-event `images` / `helpers` build but do NOT push (verify GHCR has no PR-tagged versions)
- [ ] After merge, `images.yml` / `helpers.yml` push `main` / `main-<sha>` / `sha-<long>` tags to GHCR
- [ ] Operator pushes throwaway tag `v0.0.0-test` and verifies semver tags appear on GHCR; deletes the tag
- [ ] Operator configures branch protection per `docs/conventions.md` §10.6

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Wait for first CI run, observe each workflow**

```bash
gh pr checks --watch
```

Expected outcome: all 6 checks transition to ✅ Pass.

- [ ] **Step 6: Iterate on red workflows in-place**

Common first-run failures and remedies:

| Symptom (workflow / step)                                | Likely cause                                                                        | Remedy                                                                          |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `lint / pre-commit` fails on `helpers-lock-fresh`        | Shallow checkout                                                                    | Re-confirm `lint.yml` has `fetch-depth: 0`                                      |
| `lint / pre-commit` fails on `prettier`                  | New file added in this branch not formatted                                         | `pre-commit run --all-files` locally, commit fix                                |
| `backend / pytest` import error                          | uv sync did not install dev group                                                   | Check `setup-uv` composite — `uv sync --frozen` (no `--no-dev`) is correct      |
| `frontend / unit` fails on `setup-node` cache miss       | `pnpm-lock.yaml` path mismatch                                                      | Verify `cache-dependency-path: frontend/pnpm-lock.yaml` in composite            |
| `helm / lint-template` fails on `helm dependency update` | Chart.lock missing or stale                                                         | Run locally `helm dependency update charts/lolday`, commit `Chart.lock` if bump |
| `images / build-image (frontend)` Dockerfile error       | Missing context file                                                                | Confirm `context: ./frontend` matches Dockerfile path                           |
| `helpers / build-helper (build-helper)` Dockerfile error | Subtree change unaccompanied by lock update (will only surface after merge to main) | This is a CI build, not Harbor push — should still pass docker-build-only       |
| Action SHA invalid                                       | typo                                                                                | Re-run `gh api repos/<owner>/<repo>/git/refs/tags/<tag>`                        |

Each fix: edit, `pre-commit run`, commit on the same branch, push, re-watch.

- [ ] **Step 7: Once all 6 green, ready for review**

Do not merge yet — Task 20 verifies path-filter / tag-trigger acceptance criteria using this PR.

---

## Task 20: Path-filter and tag-trigger acceptance verification

**Files:** transient test artefacts (no commits to main).

- [ ] **Step 1: docs-only path-filter test**

```bash
git checkout -b test/docs-only-path-filter
echo "# CI verification 2026-04-30" >> docs/architecture.md
pre-commit run --files docs/architecture.md
git add docs/architecture.md
git commit -m "test(ci): docs-only change to verify path filter"
git push -u origin test/docs-only-path-filter
gh pr create --title "test(ci): verify docs-only path filter" --body "Verify only lint.yml triggers on docs change. DO NOT MERGE."
gh pr checks --watch
```

Expected: only `lint / pre-commit` runs; `backend`, `frontend`, `helm`, `images`, `helpers` show «Skipped (no matching workflow)».

If correct: close the PR + delete the branch:

```bash
gh pr close test/docs-only-path-filter --delete-branch
git checkout feat/github-actions-cicd
git branch -D test/docs-only-path-filter
git push origin --delete test/docs-only-path-filter
```

If incorrect: bug in `paths-ignore` for backend/frontend or `paths` for the others. Edit on `feat/github-actions-cicd`, force-push.

- [ ] **Step 2: mlflow-server path-filter test**

```bash
git checkout -b test/mlflow-server-path-filter
echo "# CI test 2026-04-30" >> charts/lolday/helpers/mlflow-server/Dockerfile
pre-commit run --files charts/lolday/helpers/mlflow-server/Dockerfile
git add charts/lolday/helpers/mlflow-server/Dockerfile
git commit -m "test(ci): mlflow-server change to verify helpers.yml exclusion"
git push -u origin test/mlflow-server-path-filter
gh pr create --title "test(ci): verify helpers.yml excludes mlflow-server" --body "DO NOT MERGE."
gh pr checks --watch
```

Expected: `helpers / build-helper (...)` does NOT trigger; `lint` may trigger.

Close + cleanup as Step 1.

- [ ] **Step 3: Tag-trigger semver test (after `feat/github-actions-cicd` merges to main)**

After merge:

```bash
git checkout main
git pull --ff-only
git tag v0.0.0-test
git push origin v0.0.0-test
gh run watch  # observe images.yml fire on tag
```

Expected: `images.yml` runs, builds & pushes `ghcr.io/bolin8017/lolday-backend:0.0.0-test`, `:0.0`, `:0`, `:latest`. Same for frontend.

Verify on GHCR:

```bash
gh api /user/packages/container/lolday-backend/versions --jq '.[].metadata.container.tags'
```

Cleanup:

```bash
git tag -d v0.0.0-test
git push origin --delete v0.0.0-test
```

Optionally delete the GHCR PR-test tag via Settings UI or `gh api -X DELETE /user/packages/container/lolday-backend/versions/<id>`.

---

## Task 21: Branch-protection setup runbook (operator manual)

**Files:** none — operator action only.

- [ ] **Step 1: Operator opens GitHub Settings**

`https://github.com/bolin8017/lolday/settings/branches`

- [ ] **Step 2: Add branch protection rule on `main`**

Configure per `docs/conventions.md` §10.6:

1. Branch name pattern: `main`
2. Require a pull request before merging: ✅
3. Require status checks to pass before merging: ✅
   - Required: `lint / pre-commit`, `backend / pytest`, `frontend / unit`, `helm / lint-template`, `images / build-image (backend)`, `images / build-image (frontend)`, `helpers / build-helper (build-helper)`, `helpers / build-helper (job-helper)`
   - Require branches to be up to date: ✅
4. Require conversation resolution: ✅
5. Require linear history: ✅
6. Restrict who can push to matching branches → block force pushes: ✅

- [ ] **Step 3: Settings → General → Pull Requests**

Allow only «Squash merging». Disable «Merge commit» and «Rebase merging».

- [ ] **Step 4: Document any deviations in `docs/conventions.md`**

If GitHub plan tier blocks any of the required-check options, append the limitation as a footnote in `docs/conventions.md` §10.6 in a follow-up PR.

---

## Self-review notes

Spec coverage check:

| Spec section                                  | Covered by                                                                                                                                |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| §3.1 GHCR-only rationale                      | Task 12 (`.claude/rules/github-actions.md` two-registry section), Task 16 (architecture.md §6 rewrite), Task 18 (release-helpers rewrite) |
| §3.2 pre-commit single source                 | Task 5 (lint.yml), Task 12 (rules)                                                                                                        |
| §3.3 Workflow topology                        | Tasks 2–10                                                                                                                                |
| §3.4 Trigger model                            | Tasks 5–10 (per-workflow `on:` blocks)                                                                                                    |
| §3.5 Path filtering                           | Tasks 6–10 (per-workflow `paths` / `paths-ignore`)                                                                                        |
| §3.6 Image tag rules                          | Task 4 (docker-meta-build composite metadata-action tags)                                                                                 |
| §3.7 Permissions                              | Tasks 5–10 (per-workflow `permissions:`)                                                                                                  |
| §3.8 Concurrency                              | Tasks 5–10 (per-workflow `concurrency:`)                                                                                                  |
| §3.9 Runner pin                               | Tasks 5–10 (`runs-on: ubuntu-24.04`)                                                                                                      |
| §3.10 SHA pinning                             | All tasks reference the canonical SHA list at top of plan                                                                                 |
| §4.1 lint.yml                                 | Task 5                                                                                                                                    |
| §4.2 backend.yml                              | Task 6                                                                                                                                    |
| §4.3 frontend.yml + commented playwright hook | Task 7                                                                                                                                    |
| §4.4 helm.yml                                 | Task 8                                                                                                                                    |
| §4.5 images.yml                               | Task 9                                                                                                                                    |
| §4.6 helpers.yml                              | Task 10                                                                                                                                   |
| §5 Composite actions                          | Tasks 2, 3, 4                                                                                                                             |
| §Caching                                      | Tasks 5 (precommit cache), 7 (pnpm via setup-node), 8 (helm sub-chart cache); uv/buildx caches inside composites                          |
| §Dependabot                                   | Task 11                                                                                                                                   |
| §8 Documentation updates                      | Tasks 12–18                                                                                                                               |
| §First green-CI bring-up plan                 | Task 19                                                                                                                                   |
| §Acceptance criteria                          | Tasks 19, 20, 21                                                                                                                          |

No placeholders, no «similar to Task N», every code step shows actual code.

---

**Plan complete. Saved to `docs/superpowers/plans/2026-04-30-github-actions-cicd.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Tasks within Wave 1 / Wave 2 / Wave 3 can be parallelized (composite actions independent; workflows independent of each other; docs/rules independent).

**2. Inline Execution** — I execute tasks sequentially in this session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
