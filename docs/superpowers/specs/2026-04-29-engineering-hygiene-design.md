# Engineering Hygiene — Design Specification

## Overview

Lolday currently has **no automated formatting / linting discipline**:

- `backend/pyproject.toml` has zero `[tool.ruff]` / `[tool.mypy]` config (defaults only).
- No `pre-commit`, no `husky`, no `lint-staged`, no `prettier`, no `.editorconfig`.
- Frontend has ESLint flat config, but ESLint is also doing formatting work that should belong to a dedicated formatter.
- Helpers (`charts/lolday/helpers/build-helper`, `job-helper`) and `scripts/*.py` are checked by nobody.

These gaps are recorded as tech debt in `docs/architecture.md` §9 #5 and #6.

This phase introduces a single, repo-wide formatting / linting / type-check discipline that is automated via pre-commit hooks, governed by mainstream tools (ruff, mypy, prettier), and documented in path-scoped CLAUDE rules so future contributors and Claude sessions cannot quietly drift back to the pre-existing chaos.

CI integration is out of scope (deferred to a separate phase).

## Authorization

Breaking changes are explicitly authorized:

- Mass formatting changes across the repo (one separate commit, reviewable as pure auto-fix).
- Removal of any future temptation to add `black` / `flake8` / `isort` / `pylint` / `husky` / `lint-staged` / `stylelint` (these are explicitly forbidden going forward — see §9).

## Scope

### In scope

1. **Pre-commit framework** — `pre-commit.com` + `.pre-commit-config.yaml`, installed via `uv tool install pre-commit` and activated by `pre-commit install` in `scripts/install-tools.sh`.
2. **Backend lint + format** — Ruff (replaces flake8 / isort / pyupgrade and black). Repo-root `ruff.toml`. Covers `backend/`, `charts/lolday/helpers/{build-helper,job-helper}/`, `scripts/`, and `charts/lolday/files/deadmans_switch/check.py`.
3. **Backend type check** — mypy. Repo-root `mypy.ini`. First wave scans `backend/app/` only (lenient: `strict = false`, a handful of `warn_*` and `check_untyped_defs` enabled).
4. **Frontend format** — Prettier 3.x at repo root (`.prettierrc.json` + `.prettierignore`); `eslint-config-prettier` integrated into existing flat ESLint config; `pnpm format` / `pnpm format:check` scripts in `frontend/package.json`.
5. **Cross-language consistency** — `.editorconfig` at repo root.
6. **First auto-fix pass** — applied as a separate `style:` commit, with diff preview shared before apply.
7. **Documentation** — update `.claude/rules/{backend,frontend,scripts-and-ops}.md`; add a section to root `CLAUDE.md`; mark `docs/architecture.md` §9 #5 + #6 resolved; record a new debt entry for "mypy module-level overrides to be cleaned up".

### Out of scope

- CI / GitHub Actions integration (separate phase).
- Modifying any application logic. The third commit (manual lint-error fixes) is restricted to style-equivalent edits (unused imports, redundant casts, pyupgrade-equivalent rewrites that ruff can't auto-fix).
- Type stubs for `mlflow-skinny`, `kubernetes`, etc. — handled by `ignore_missing_imports`.
- Refactoring `backend/app/reconciler.py` (57 KB, separate phase).
- Stylelint for CSS, husky, lint-staged, commitlint, prettier-eslint integration layer.
- IDE-specific config files committed to the repo (`.vscode/settings.json` etc.).
- Multi-OS / Windows support.

## Architecture

### Tool selection and what each replaces

| Domain                      | Adopted                     | Replaces / role                                                                                        |
| --------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------ |
| Pre-commit framework        | `pre-commit.com`            | De-facto standard for cross-language monorepos; not npm-only                                           |
| Python lint                 | `ruff` (lint)               | flake8 / pylint / isort / pyupgrade                                                                    |
| Python format               | `ruff format`               | black (Astral's official black-compat replacement, 30–100× faster)                                     |
| Python type check           | `mypy`                      | (new) — chosen over pyright for richer pyproject / pre-commit integration and broader plugin ecosystem |
| Frontend format             | `Prettier 3.x`              | (new)                                                                                                  |
| Frontend lint               | existing ESLint flat config | unchanged; gains `eslint-config-prettier` to disable formatting rules                                  |
| Cross-language indent / EOL | `.editorconfig`             | (new)                                                                                                  |

**Forbidden going forward** (do not reintroduce): black, flake8, pylint, isort, autopep8, yapf, husky, lint-staged, stylelint, commitlint, prettier-eslint.

### Pre-commit hook layering

All hooks run on `pre-commit` stage (no `pre-push` split). Empirically the full suite finishes in under 10 seconds on a 125-file backend; splitting stages introduces cognitive overhead without benefit. Re-evaluate if mypy ever exceeds 5s warm.

| Hook                                                                                                                                                                                | Source                                       | Files                                                            | Purpose         |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------- | --------------- |
| `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-json`, `check-merge-conflict`, `check-case-conflict`, `mixed-line-ending`, `check-added-large-files` | `pre-commit/pre-commit-hooks`                | repo-wide (yaml excludes `charts/lolday/templates/`)             | Hygiene basics  |
| `ruff check --fix`                                                                                                                                                                  | `astral-sh/ruff-pre-commit` (hermetic)       | `*.py`                                                           | Lint + auto-fix |
| `ruff format`                                                                                                                                                                       | `astral-sh/ruff-pre-commit` (hermetic)       | `*.py`                                                           | Format          |
| `mypy`                                                                                                                                                                              | local hook (`uv run --project backend mypy`) | `backend/app/`                                                   | Type check      |
| `prettier --write`                                                                                                                                                                  | local hook (`pnpm --dir frontend prettier`)  | `*.{ts,tsx,js,jsx,css,json,md,yaml,yml}` minus `.prettierignore` | Format          |
| `eslint --fix --no-warn-ignored`                                                                                                                                                    | local hook (`pnpm --dir frontend eslint`)    | `frontend/**/*.{ts,tsx,js}`                                      | Lint + auto-fix |

Hermetic vs local rationale:

- **Ruff** is a self-contained binary with no project deps → hermetic mirror is fastest and reproducible.
- **mypy / prettier / eslint** must see project deps to resolve types and config plugins → local hook reusing the project's existing venv is the mainstream monorepo pattern (Pydantic, FastAPI, Posthog, Dagster).

### Config file layout

```
lolday/
├── .editorconfig                 # NEW
├── .pre-commit-config.yaml       # NEW
├── .prettierrc.json              # NEW
├── .prettierignore               # NEW
├── ruff.toml                     # NEW — single source of truth across all .py
├── mypy.ini                      # NEW
├── backend/
│   └── pyproject.toml            # mod: add mypy to [dependency-groups].dev
│                                 #      DO NOT add [tool.ruff] / [tool.mypy] (would shadow root)
├── frontend/
│   ├── eslint.config.js          # mod: append eslint-config-prettier
│   └── package.json              # mod: add prettier + eslint-config-prettier devDeps; add format / format:check scripts
└── charts/lolday/helpers/{build-helper,job-helper}/pyproject.toml
                                  # unchanged — root ruff.toml owns lint config
```

Why root-level `ruff.toml` and `mypy.ini` rather than a single top-level `pyproject.toml`:

- Repo has 4 Python project boundaries (backend, build-helper, job-helper, miscellaneous scripts). Ruff resolves config from the closest ancestor; placing config in `backend/pyproject.toml` would silently leave helpers and scripts unconfigured.
- Creating a new top-level `pyproject.toml` purely to host tool config introduces a no-op project package — `ruff.toml` and `mypy.ini` are the recommended file-based alternative in Ruff and mypy docs.

### Ruff configuration (lint rules)

```toml
# ruff.toml
target-version = "py312"
line-length = 88
extend-exclude = [
    "backend/.venv",
    "backend/migrations/versions",
    "**/.venv",
    "frontend",
    "charts/lolday/charts",
]

[lint]
select = ["E", "W", "F", "I", "B", "UP", "C4", "SIM", "RUF"]
ignore = [
    "E501",   # line length is enforced by ruff format
    "B008",   # FastAPI Depends() default is intentional pattern
    "SIM108", # ternary often hurts readability
]

[lint.per-file-ignores]
"backend/tests/**" = ["S101", "B017"]
"scripts/**" = ["T20"]

[format]
# default: black-compatible
```

Rule-set rationale:

- `E / W / F / I / B / UP / C4 / SIM / RUF` is the Pydantic-style baseline. All nine groups are auto-fix-heavy and align with modern Python idioms.
- Explicitly **not** enabled in this phase: `S` (bandit — false-positive heavy), `N` (pep8-naming — flags many legitimate existing names), `T20` (no-print — punishes scripts that legitimately print), `D` (pydocstyle — large lift for unclear ROI), `ANN` (annotations — overlaps with mypy strictness ladder).
- `line-length = 88` matches existing reality: 8124 of 8142 backend lines already fit (99.8 %).
- Alembic migrations are excluded because the templates are auto-generated and follow patterns that frequently violate `B`-class rules without bug risk.

### mypy configuration (type check)

```ini
[mypy]
python_version = 3.12
files = backend/app
plugins = pydantic.mypy
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
check_untyped_defs = true
no_implicit_optional = true
strict_equality = true
# First-wave deliberately not set: disallow_untyped_defs, disallow_incomplete_defs, disallow_any_*

[mypy-backend.app.migrations.*]
ignore_errors = true

[mypy-mlflow.*]
ignore_missing_imports = true
[mypy-kubernetes.*]
ignore_missing_imports = true
[mypy-fakeredis.*]
ignore_missing_imports = true
[mypy-respx.*]
ignore_missing_imports = true
```

Strictness rationale:

- The `warn_*` group catches real bugs (unused ignores, redundant casts) without flagging existing untyped functions.
- `check_untyped_defs = true` lints inside untyped functions, raising the floor without forcing all signatures.
- Aggressive flags (`disallow_untyped_defs` etc.) are excluded in this phase. Each module that has `ignore_errors = true` becomes a tracked debt item; future phases can incrementally remove the override.

### Prettier configuration

```json
{
  "semi": true,
  "singleQuote": false,
  "printWidth": 80,
  "tabWidth": 2,
  "trailingComma": "all",
  "proseWrap": "preserve"
}
```

```
# .prettierignore
frontend/src/api/schema.gen.ts
frontend/dist
frontend/node_modules
frontend/test-results
backend/.venv
**/pnpm-lock.yaml
**/package-lock.json
backend/uv.lock
charts/lolday/templates/
charts/lolday/charts/
charts/lolday/dashboards/
```

Why these defaults:

- `printWidth: 80` is Prettier's official recommendation. Empirical impact: ~190 lines re-wrap on first run, all of them benign JSX continuations.
- `singleQuote: false` matches existing `eslint.config.js` double-quote style.
- `proseWrap: "preserve"` keeps existing markdown paragraph structure, avoiding doc churn.
- `charts/lolday/templates/` is excluded because Helm templates contain `{{ .Release.Namespace }}` Go-template syntax that Prettier mangles.
- `charts/lolday/dashboards/` is excluded because Grafana dashboard JSON is an external export format and reformatting would generate dashboard diff noise without value.

### `.editorconfig`

```ini
root = true

[*]
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
charset = utf-8
indent_style = space
indent_size = 2

[*.py]
indent_size = 4

[Makefile]
indent_style = tab

[{*.md,*.mdx}]
trim_trailing_whitespace = false
```

Recognised by VSCode (built-in) and JetBrains IDEs out of the box.

## First Auto-Fix Pass — Commit Strategy

The auto-fix pass is the highest-risk step (touches every file). Strategy: split into three reviewable commits.

| Commit | Title                                          | Contents                                                                                                                                                                                 | Reviewer focus                                                  |
| ------ | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **C1** | `chore: introduce engineering hygiene tooling` | All new config files, `backend/pyproject.toml` dev dep change, `frontend/package.json` + `eslint.config.js` mods, `scripts/install-tools.sh` augmentation. **Zero source-code changes.** | Config correctness                                              |
| **C2** | `style: apply ruff and prettier auto-fix`      | `ruff check --fix` + `ruff format` + `prettier --write` + `eslint --fix` outputs. **Zero logic changes.**                                                                                | Diff is purely whitespace / quotes / import order / quote style |
| **C3** | `fix: resolve lint errors not auto-fixable`    | Manual fixes for ruff / eslint / mypy errors that auto-fix did not handle. Skipped if no errors remain.                                                                                  | Behaviour-preserving manual edits                               |

### Pre-flight diff preview

Before applying C2:

1. Land C1 on a feature branch.
2. Run `ruff check --fix --diff` + `ruff format --diff` + `pnpm prettier --check` and capture output to `.engineering-hygiene-preview.diff` at repo root (gitignored).
3. Operator reviews the preview file.
4. Apply for real, commit as C2.
5. Run all checks without `--fix`, list remaining errors, hand to operator.
6. Operator decides each remaining error → C3.

### Mypy first-wave handling

mypy does not auto-fix. Expected paths after C1 lands:

- **≤ ~20 errors total**: fix manually inside C3 (no debt added).
- **> ~20 errors**: do not adopt the niche `mypy_baseline` tool. Instead, add `[mypy-<module>] ignore_errors = true` per offending module, then in `docs/architecture.md` §9 record a new debt: "mypy module-level overrides pending cleanup". Each future phase touching that module is responsible for removing its override.

Known candidate for an `ignore_errors` override: `backend.app.reconciler` (57 KB existing tech debt; will not be type-cleaned in this phase).

This is the **only deliberate trade-off** in the design — incremental enablement is the mainstream monorepo pattern (recommended by mypy docs) for type-checking adoption on existing codebases.

### Failure rollback for C2

If review of C2 reveals an undesired transformation:

1. **First**: tune `ruff.toml` (`magic-trailing-comma`, `line-ending`, etc.) and re-run.
2. **Second**: add `# fmt: off` / `# fmt: on` block in the affected file. This is ruff/black's official mechanism for "intentional layout preservation" and does not constitute a `# noqa` escape.
3. **Last resort**: `git revert` C2, return to a C1-only branch state, and renegotiate the design.

## Bootstrap and Developer Experience

### Installation path (no sudo, user-level only)

| Tool                                 | Install command                                       | Location                        |
| ------------------------------------ | ----------------------------------------------------- | ------------------------------- |
| `pre-commit`                         | `uv tool install pre-commit`                          | `~/.local/bin/pre-commit`       |
| `ruff`                               | pre-commit-managed (hermetic)                         | `~/.cache/pre-commit/repos/...` |
| `mypy`                               | `uv add --group dev mypy`                             | `backend/.venv`                 |
| `prettier`, `eslint-config-prettier` | `pnpm add -D`                                         | `frontend/node_modules`         |
| Hook activation                      | `pre-commit install` (writes `.git/hooks/pre-commit`) | repo-local                      |

`uv tool install pre-commit` replaces the older `pipx install pre-commit` recommendation; uv tool is the new mainstream Python-CLI user-level installer and the project already standardises on uv.

### Augment `scripts/install-tools.sh`

Append:

```bash
if ! command -v pre-commit >/dev/null 2>&1; then
  uv tool install pre-commit
else
  uv tool upgrade pre-commit || true
fi

(cd "$REPO_ROOT" && pre-commit install)
```

This is the single point that makes the discipline "always on" — `pre-commit install` writes a hook into `.git/hooks/pre-commit` so every `git commit` runs the suite.

### Developer-facing commands

`frontend/package.json`:

```json
{
  "scripts": {
    "format": "prettier --write .",
    "format:check": "prettier --check ."
  }
}
```

Backend has no wrapper scripts. Use the tools directly:

```bash
cd backend
uv run ruff check .
uv run ruff format .
uv run mypy
```

### Whole-repo trigger (CI placeholder)

```bash
pre-commit run --all-files
```

Documented in `.claude/rules/scripts-and-ops.md`. No wrapper script — calling `pre-commit` directly is the mainstream interface.

### Cannot-bypass discipline

- `git commit --no-verify` is forbidden by hard rule (root `CLAUDE.md`).
- `|| true` inside hook scripts is forbidden — hook failure must surface.
- `# noqa: <code>` and `# type: ignore[<code>]` must be accompanied by a same-line reason comment; bare suppressions are forbidden.
- `# fmt: off` / `# fmt: on` blocks are permitted (ruff-supported, behaviour-equivalent to black) and do not require justification beyond a brief comment if the layout intent is non-obvious.

## Documentation Updates

### `.claude/rules/backend.md`

New "Lint / Format / Type-check 紀律" section:

- Tooling: ruff (lint + format), mypy
- Config truth lives at `ruff.toml` and `mypy.ini` in repo root, **not** `backend/pyproject.toml`
- Manual commands (`uv run ruff check / format`, `uv run mypy`)
- Forbidden additions: black, flake8, pylint, isort, autopep8, yapf
- Rules: don't shadow root config; expanding `ignore` is forbidden; bare `# type: ignore` is forbidden; mypy strictness is incrementally enabled by removing `[mypy-<module>] ignore_errors = true` overrides as part of phases that touch each module

### `.claude/rules/frontend.md`

New "Format 紀律" section:

- Prettier (formatter) + ESLint (linter) — strict role separation
- Config: `.prettierrc.json`, `.prettierignore` at repo root; `eslint-config-prettier` integrated into flat config
- Commands: `pnpm format`, `pnpm format:check`, `pnpm lint`, `pnpm typecheck`
- Forbidden additions: stylelint, husky, lint-staged, commitlint, prettier-eslint
- Rules: don't re-enable formatting rules in ESLint (prettier owns formatting); existing CSP hard rule unchanged

### `.claude/rules/scripts-and-ops.md`

New "Engineering hygiene 紀律" section:

- Repo-wide: `pre-commit run --all-files`
- Hook upgrade: `pre-commit autoupdate` (optional, quarterly cadence)
- Install: `uv tool install pre-commit && pre-commit install` (already wired into `scripts/install-tools.sh`)
- Forbid `git commit --no-verify`
- New `.py` scripts must conform to the root `ruff.toml`

### Root `CLAUDE.md`

- Quickstart commands: append `pre-commit run --all-files` line
- New hard rule "Lint / format 不繞過": `--no-verify` forbidden, `# noqa` and `# type: ignore` require reason comment

### `docs/architecture.md` §9

- Mark #5 (no pre-commit / prettier / .editorconfig) as **resolved 2026-04-29**
- Mark #6 (no `[tool.ruff]` / `[tool.mypy]` config) as **resolved 2026-04-29**
- Add new debt entry: "mypy module-level overrides pending cleanup — large modules (e.g. `reconciler.py`) currently have `ignore_errors = true`; remove per-module as phases touch them"

### Not updated

- `README.md` — README targets external readers; tool discipline is internal
- `docs/runbooks/deploy.md` — discipline does not affect deploy flow
- `docs/conventions.md` — naming / branch conventions are orthogonal to lint discipline
- No new `docs/runbooks/engineering-hygiene.md` — path-scoped CLAUDE rules are authoritative; a parallel doc would diverge

## Risks, Edge Cases, and Mitigations

### Risk register

| Risk                                                   | Likelihood                          | Impact                      | Mitigation                                                                                                                       |
| ------------------------------------------------------ | ----------------------------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Helm template broken by Prettier                       | High if `.prettierignore` misses it | Deploy fails                | `.prettierignore` excludes `charts/lolday/templates/`; post-C2 validation runs `helm lint charts/lolday`                         |
| Ruff format destroys intentional alignment             | Low–medium                          | Code readability regression | C2 diff is reviewed before apply; surgical `# fmt: off` blocks are permitted                                                     |
| First-wave mypy errors block C3                        | Medium                              | C3 PR drags                 | Per-module `ignore_errors` per the §First Auto-Fix Pass policy; recorded as new debt                                             |
| `pydantic.mypy` plugin conflicts with SQLAlchemy 2.0   | Low                                 | mypy fails to start         | Pydantic v2 + SA 2.0 is the mainstream stack and known compatible; fallback is to drop the plugin and lower strictness one notch |
| Pre-commit cold cache makes first commit slow (~30s)   | High (one-time)                     | Onboarding friction         | After install, run `pre-commit run --all-files` to pre-warm cache                                                                |
| `schema.gen.ts` rewritten by Prettier                  | Low                                 | Regen-diff noise            | Excluded in `.prettierignore`                                                                                                    |
| `eslint-config-prettier` flat-config integration error | Medium                              | ESLint won't run            | Follow Prettier docs flat-config example exactly: append config object as last array element                                     |
| `uv tool install pre-commit` on offline machine        | Low                                 | install-tools fails         | server30 is online; offline dev can use `uv tool install --offline` from local cache                                             |

### Edge cases

- **Alembic auto-generated migrations**: `backend/migrations/versions/` is excluded from ruff and from mypy's effective scope.
- **Helper-image Python**: `build-helper/maldet_validator.py` and `job-helper/job_helper/**` are linted by the same root `ruff.toml`; mypy first wave does not cover them.
- **`charts/lolday/files/deadmans_switch/check.py`**: linted by ruff. No conflict with current rule selection (`T20` not enabled).
- **Existing git hooks**: `.git/hooks/pre-commit` does not exist (no `.husky` directory, working tree clean as of 2026-04-29). `pre-commit install` writes a fresh hook with no overwrite risk.
- **Multi-OS**: team runs Linux only; Windows is not a target.

## Acceptance Criteria

This phase is complete when:

1. `pre-commit run --all-files` exits 0 on a clean working tree from `main` after the three commits land.
2. `helm lint charts/lolday` still passes (i.e., Prettier did not touch templates).
3. `cd backend && uv run pytest` passes.
4. `cd frontend && pnpm typecheck && pnpm lint && pnpm format:check && pnpm test` all pass.
5. A fresh clone followed by `bash scripts/install-tools.sh` successfully installs pre-commit and activates the hook.
6. `git commit` on a noop change triggers the suite and exits 0; `git commit --no-verify` is documented as forbidden in CLAUDE.md hard rules.
7. `docs/architecture.md` §9 reflects the resolution of #5 + #6 and the new mypy-overrides debt entry.
8. `.claude/rules/{backend,frontend,scripts-and-ops}.md` carry the new sections described in §Documentation Updates.

## Open Questions

None. All design decisions are resolved.
