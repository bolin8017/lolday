# Conventions

> Source spec: `docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md` §7.
> Effective from 2026-04-29. Pre-existing commits, branches, and filenames are NOT rewritten.
>
> 2026-04-29 update: `phaseN-X` numbering is **retired** for new artefacts. See §4 for why and the date-based replacement.

## 1. Branch naming (mainstream)

`<type>/<short-kebab-desc>`

Examples:

- `feat/job-detail-tabs`
- `fix/role-enum-lowercase`
- `chore/bump-deps`
- `docs/restructure-claude-md`
- `refactor/reconciler-split`

Allowed types: `feat | fix | chore | docs | refactor | test | perf | build | ci`.

## 2. Commit messages — Conventional Commits

Format: `<type>(<scope>): <subject>`

Examples:

- `feat(jobs): add detail summary tab`
- `fix(auth): align role_enum to values_callable`
- `chore(charts): bump kube-prometheus-stack to 84.4.0`

Rules:

- `scope` is a module name (`jobs`, `auth`, `reconciler`, `harbor`, `charts`, `frontend`, `backend`, `migrations`, `rules`, `docs`). It is **not** a phase number.
- `subject` is imperative, lowercase, no trailing period.
- Body is optional but encouraged for non-trivial changes; wrap at 72 columns.
- Footer for `Co-Authored-By:` and `Closes #N`.

Multi-commit branches: each commit follows the format. PR title equals the most representative commit's message (squash-merge friendly).

## 3. Pull requests

PR title format: same as a Conventional Commit.

PR description must include the spec/plan link when one exists:

```
Spec: docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md
Plan: docs/superpowers/plans/YYYY-MM-DD-<topic>.md
```

PRs without a spec are acceptable for hotfixes and small changes (≤ a couple of files, low coordination cost).

## 4. Spec / plan filenames — date + topic, no phase numbers

Format: `YYYY-MM-DD-<short-kebab-desc>-design.md` (specs), `YYYY-MM-DD-<short-kebab-desc>.md` (plans). Examples:

- `docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md`
- `docs/superpowers/plans/2026-04-29-claude-md-restructure.md`

**Phase numbering (`phaseN`, `phaseN-X`) is retired.** Why:

- It worked for Phase 1–6 (the original sequential platform build) and 11a–e (a coherent detector-framework migration).
- It broke down from Phase 12 onward, where unrelated fixes were bundled into one "phase" (Phase 12: orphan-vcjob reconciler + chart hygiene + service-token notify skip — three independent concerns) and hotfixes were forced into invented sub-phases (Phase 12.1, 12.2, 12.3 are three sequential patches against one role_enum bug — that's smell).
- Mainstream OSS projects don't have a phase concept; they use Conventional Commits + dates + spec-on-demand. Lolday is past the initial sequential build and into iterative-improvement mode, so the same applies.
- Date-based filenames sort chronologically, are unambiguous, and never run into "is this its own phase or a sub-phase of the last one?" debates.

Hotfixes that don't merit a spec use `fix/<short-desc>` and (if post-mortem-worthy) get a `docs/postmortems/YYYY-MM-DD-<topic>.md`. **Never invent phase numbers** to host a hotfix or to backfill structure that isn't there.

History is preserved: existing `phaseN-X` filenames in `docs/superpowers/{specs,plans}/`, alembic migrations, and `docs/phase-history/` stay as-is and are referenced as historical names. `docs/architecture.md` §8 lists them as the legacy phase progression.

## 5. Cut-over

These conventions apply from **2026-04-29 forward**. Pre-existing commits, branches, spec/plan filenames, and migration filenames keep their original form; we don't rewrite history.

## 6. Migration filenames — date or alembic default, no phase prefix

For new alembic revisions, use alembic's auto-generated filename (`<rev>_<short_desc>.py`) without renaming. The phase-prefix rename rule that was in force through Phase 13 is retired alongside §4. See `.claude/rules/alembic-migrations.md` for the full guidance, including the historical phase-mapping table.

## 7. Code naming

- **Python**: `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_SNAKE` for module constants.
- **Kubernetes resources / Helm values keys**: `kebab-case`.
- **TypeScript / React**: `camelCase` for variables, `PascalCase` for components, `kebab-case` for filenames (or `PascalCase.tsx` for component files — match the surrounding directory).

## 8. Three test layers

- `backend/tests/` — pytest (unit, service, reconciler, migrations). Run: `cd backend && uv run pytest`.
- `frontend/tests/unit/` — vitest. Run: `cd frontend && pnpm test`.
- `frontend/tests/e2e/` — playwright (some tests need the backend up). Run: `cd frontend && pnpm playwright test`.
- `tests/phase7/` — shell-based integration smokes (alertmanager, volcano queue, ServiceMonitor presence). Run individually; not gated by anything.

## 9. Before writing new code

Read the path-scoped rule for the area you're touching:

- `backend/...` → `.claude/rules/backend.md`
- `frontend/...` → `.claude/rules/frontend.md`
- `charts/...` → `.claude/rules/charts-and-helm.md`
- `scripts/...` → `.claude/rules/scripts-and-ops.md`
- `backend/migrations/...` → `.claude/rules/alembic-migrations.md`

## 10. CI / CD (GitHub Actions)

> Source spec: `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md`. Detailed discipline lives in `.claude/rules/github-actions.md`.

### 10.1 Workflow inventory

| Workflow             | Triggers                                                          | What it does                                                                                                                                |
| -------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `lint.yml`           | every push to `main`, every PR                                    | `pre-commit run --all-files` (single source of truth for ruff / mypy / prettier / eslint)                                                   |
| `backend-fast.yml`   | path-filtered to `backend/**`                                     | `cd backend && uv run pytest -m "not heavy"` — schemathesis contract tier + unit / integration tier                                         |
| `backend-slow.yml`   | path-filtered + schedule                                          | `pytest -m heavy` — testcontainers MLflow / Postgres / JWKS reflector (slow, not branch-protection-required)                                |
| `frontend.yml`       | path-filtered to `frontend/**`                                    | `pnpm typecheck` + `pnpm test` (vitest) + OpenAPI snapshot regen drift guard                                                                |
| `frontend-slow.yml`  | path-filtered + schedule                                          | Playwright E2E (uvicorn + vite via webServer; `SPEC_LANE_STUBS=true` for K8s / MLflow / Postgres stubs)                                     |
| `chart-e2e.yml`      | path-filtered to `charts/**` / Kyverno / PSS                      | kind cluster: helm install + Kyverno admission + PSS enforce smoke                                                                          |
| `helm.yml`           | path-filtered to `charts/**`                                      | `helm dep update` + `helm lint` + `helm template`                                                                                           |
| `images.yml`         | `backend/Dockerfile` / `frontend/Dockerfile` paths + tag `v*.*.*` | matrix build backend / frontend → GHCR (PR builds only, no push) + cosign sign + SLSA attest-build-provenance on main / tag pushes          |
| `helpers.yml`        | path-filtered to `charts/lolday/helpers/{build,job}-helper/**`    | matrix build → GHCR (cosign sign + SLSA attest-build-provenance on main / tag); `mlflow-server` and `pytorch-cu12-base` are operator-manual |
| `bats.yml`           | path-filtered to `scripts/**`                                     | bats unit tests for `scripts/lib/` shell scripts (D4.1)                                                                                     |
| `gitleaks.yml`       | every PR                                                          | secret-scan gate via `gitleaks/gitleaks-action` (config: repo-root `.gitleaks.toml`)                                                        |
| `dispatch.yml`       | every PR                                                          | path-filter dispatcher governing the per-area required-check matrix (see §10.6)                                                             |
| `mutation.yml`       | scheduled (weekly)                                                | mutmut cron on the top-10 backend modules (D4.3)                                                                                            |
| `test-telemetry.yml` | scheduled (weekly)                                                | JUnit XML ingest → `docs/test-telemetry/dashboard.md` (D4.4)                                                                                |
| `flaky-tracker.yml`  | scheduled (weekly)                                                | aggregates `flaky_tracked` test failure rate; opens issues at >1 % weekly fail rate (D1.13)                                                 |
| `trivy-cron.yml`     | scheduled                                                         | scheduled Trivy image scan of the published `ghcr.io/bolin8017/lolday-*` set                                                                |
| `*-skip.yml` (× 5)   | path-filter skip path                                             | satisfy the required-check contract when path filters skip the real job; raw `check_run.name` matches the real workflow (see §10.6)         |

### 10.2 Pre-commit is the single source of truth

The CI's `lint.yml` runs `pre-commit run --all-files` against the same `.pre-commit-config.yaml` operators run locally. Do **not** add parallel `uv run ruff check` / `pnpm lint` steps to other workflows — duplication breeds drift. To add a new check, edit `.pre-commit-config.yaml`; CI follows automatically.

### 10.3 Two-registry model

- **GHCR** — `ghcr.io/bolin8017/lolday-{backend,frontend,build-helper,job-helper}`. Populated by CI on `main` and tag pushes. Verification artefact.
- **Harbor** — `harbor.lolday.svc:80/lolday/*`. Production runtime registry, server30-internal. Populated by operator running `bash scripts/build-helpers.sh` and the parallel manual flows for backend / frontend / mlflow-server / pytorch-cu12-base. CI never pushes here.

Why split: Harbor is unreachable from GitHub-hosted runners by design (`docs/architecture.md` §5.3); see spec §3.1 for the rejection of self-hosted-runner / Cloudflare-Tunnel-Harbor alternatives.

### 10.4 Image tag rules

| Trigger            | Tags applied                                 |
| ------------------ | -------------------------------------------- |
| `push: main`       | `main`, `main-<short-sha>`, `sha-<long-sha>` |
| `push: tag v1.2.3` | `1.2.3`, `1.2`, `1`, `latest`                |
| `pull_request`     | not pushed (build only)                      |

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

> **Footnote — status update (2026-05-15 public flip).** Path 1 above was chosen: the repo went public on 2026-05-15 after the post-program review (`docs/phase-history/2026-05-15-security-post-program-review.md`). Branch protection on `main` is now **active**: PR required, no force-push, no delete, linear history; `required_approving_review_count: 0` (single-operator project). The supplementary controls — GitHub Secret Scanning + Push Protection, Dependabot Security Updates, private vulnerability reporting — were enabled at the same time. The `gitleaks` workflow (`.github/workflows/gitleaks.yml`) runs on every PR as an additional pre-merge secret-scan gate.
>
> Mechanical chart / doc PRs may be admin-merged after local verification when CI billing is blocked (project precedent — see `feedback_gha_billing_can_block_ci.md` in auto-memory).

### 10.7 Dependabot SOP

Weekly Mondays. Per ecosystem:

- **`github-actions`** — bumps SHA pin + comment in lock-step. Green CI = squash merge.
- **`pip`** (backend) — minor/patch grouped; verify `cd backend && uv lock` aligns with the merged `pyproject.toml`.
- **`npm`** (frontend) — minor/patch grouped; check peer-dep warnings in CI log.
- **`docker`** (per Dockerfile dir) — base-image bump. For `mlflow-server` and `pytorch-cu12-base`, merging the PR is half the work — operator must rebuild manually because their CI build is out of scope.
