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
