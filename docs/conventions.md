# Conventions

> Source spec: `docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md` §7.
> Effective from 2026-04-29. Pre-existing commits and branches are NOT rewritten.

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
Spec: docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md
Plan: docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md
```

PRs without a spec are acceptable for hotfixes and tiny doc fixes.

## 4. Phase numbering — only in planning docs

Phase numbers (`phaseN-X`) live in:
- `docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md`
- `docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md`
- PR descriptions (as `Spec:` / `Plan:` pointers)

They do **not** appear in branch names, commit subjects, or commit scopes.

Hotfixes that don't belong to a phase use `fix/<short-desc>` and (if post-mortem-worthy) get a `docs/postmortems/YYYY-MM-DD-<topic>.md`. Never invent sub-phases like `phase12.1.1` to host a hotfix.

## 5. Cut-over

These conventions apply from **2026-04-29 forward**. Pre-existing commits and branches keep their original form; we don't rewrite history.

## 6. Migration filename convention

See `.claude/rules/alembic-migrations.md`.

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
