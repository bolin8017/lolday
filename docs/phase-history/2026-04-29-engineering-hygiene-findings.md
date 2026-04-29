# Engineering Hygiene — Execution Retrospective

**Date:** 2026-04-29
**Outcome:** ✅ Two PRs squash-merged: #24 (`chore: introduce engineering hygiene`) and #25 (`chore(hygiene): pre-commit autoupdate`). `pre-commit run --all-files` exits 0 with no `SKIP` and no `--no-verify`. Resolves `docs/architecture.md` §9 #5 + #6; activates §9 #11 (mypy reconciler override as tracked debt).

## Summary

PR #24 wired up `pre-commit` and a single repo-wide formatting / linting / type-check discipline (ruff lint + format, mypy, prettier, eslint-config-prettier, .editorconfig, pre-commit-hooks built-ins). The codebase was auto-fixed in a separate `style:` commit so that the diff was reviewable in isolation; remaining non-auto-fixable findings (77 ruff, 35 eslint, 23 mypy) were resolved in C3 with a mix of root-cause fixes, config tweaks, and one tracked module-level mypy override on `app.reconciler` (the 57 KB monster the spec explicitly defers).

PR #25 was the immediate quarterly autoupdate (caught by the final-review pass): `pre-commit/pre-commit-hooks` v5 → v6, `astral-sh/ruff-pre-commit` v0.9.2 → v0.15.12, and the canonical `id: ruff-check` rename. Zero new findings on the cleaned-up codebase.

## Plan-vs-execution surprises caught by review

The two-stage review (spec compliance + code quality after each phase) caught seven issues before they leaked into `main`. Each is a useful planning lesson.

| #   | Issue                                                                                                                                                                                                                              | Caught by                          | Fix path                                                                                                                                                            |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Plan's `[1/3] → [3/4]` find/replace was incomplete — left `[1/3] kubectl` and `[2/3] helm` untouched in `scripts/install-tools.sh`.                                                                                                | Phase 1 spec reviewer              | New commit `40a03bd` patched the prefixes.                                                                                                                          |
| 2   | Spec template embedded `[mypy-backend.app.migrations.*]` as a sample override — but `backend/migrations/` is the alembic root, **not** `backend/app/migrations/`. The override matched nothing.                                    | Phase 1 code-quality reviewer      | Removed in `e1fbe29`.                                                                                                                                               |
| 3   | mypy module name surprise: with `files = backend/app`, mypy treats `backend/app/` as the package root, so the dotted module path is `app.reconciler`, not `backend.app.reconciler`. The plan's example was wrong; impl is correct. | Phase 4 spec reviewer              | Comment in `mypy.ini` aligned in `71b584b`.                                                                                                                         |
| 4   | Grafana dashboard JSON files were modified by `end-of-file-fixer` (added trailing newline). `.prettierignore` only governs prettier; pre-commit-hooks built-ins need their own `exclude:` regex.                                   | Phase 2 implementer (self-flagged) | `3be39c2` added `exclude: ^charts/lolday/dashboards/` to `trailing-whitespace` and `end-of-file-fixer`.                                                             |
| 5   | `pnpm format:check` failed with 3 files even after `pre-commit run --all-files` was clean. Root cause: pre-commit prettier hook runs from repo-root CWD (sees `.prettierignore`), but `pnpm scripts` from `frontend/` CWD don't.   | Phase 4 + final reviewer           | `37b2178` added `--ignore-path ../.gitignore --ignore-path ../.prettierignore` to the format scripts; also added `html` to the hook's `files:` regex (was missing). |
| 6   | When `# noqa: E402  # reason` made an import line >88 chars, `ruff format` wrapped to multi-line and the noqa attachment was dropped inconsistently. Adding inline reason comments to existing bare `# noqa` lines back-fired.     | Self-discovery during fix          | `aefe5e1` removed inline `# noqa` and used `[lint.per-file-ignores]` in `ruff.toml` instead — deliberate code-organization patterns belong in config, not per-line. |
| 7   | ESLint hook running from repo-root CWD didn't find `frontend/eslint.config.js`. Hook was a no-op for the entire Phase 2 run.                                                                                                       | Phase 2 implementer (self-flagged) | `3be39c2` added `--config frontend/eslint.config.js` to the hook entry.                                                                                             |

## What worked

- **Three-commit split (C1 / C2 / C3).** C1 was config-only (zero source edits), C2 was pure auto-fix (zero hand edits), C3 was manual lint resolution. Each commit was reviewable in isolation, and a diff preview after C1 (saved to gitignored `.engineering-hygiene-preview.diff`) gave the operator a chance to abort before C2 landed.
- **`SKIP=ruff,eslint,mypy git commit` for C2** instead of `--no-verify`. Preserves the discipline (other hooks still run; bypass is targeted and documented in commit body) while letting C2 commit cleanly with deferred lint findings owned by C3.
- **Subagent-driven-development with two-stage review** caught all seven plan-vs-execution issues above before any of them reached `main`. Spec compliance review (cheap) flagged content gaps; code-quality review (also cheap) flagged subtle issues like the `app.reconciler` vs `backend.app.reconciler` module-path bug.
- **Root-cause fixes over band-aids** consistently. RUF006 (fire-and-forget `asyncio.create_task`) got reasoned `# noqa` because the spec explicitly forbids refactoring reconciler. Everything else was real fix or config-level tweak.

## Tech-debt and follow-ups

- **`[mypy-app.reconciler] ignore_errors = true`** is active. Removal procedure in `docs/architecture.md` §9 #11 (delete the block, run mypy, fix Optional-handling at root cause, verify pre-commit clean).
- **Prettier v4** will deprecate the `--ignore-path` CLI flag in favour of an `ignorePath` option in `.prettierrc.json`. `frontend/package.json`'s `format` / `format:check` scripts need updating when the upgrade happens.
- **`pre-commit autoupdate`** quarterly per `.claude/rules/scripts-and-ops.md`. Last run: 2026-04-29 in #25. Next expected: ~2026-07-29.

## Forward planning lessons

- **When writing a plan that includes find/replace blocks**, audit every occurrence of the old string in the file, not just the one you're focused on. Lesson #1 above came from changing `[3/3]` to `[3/4]` while leaving the earlier `[1/3]` and `[2/3]` to drift.
- **When sampling spec text from another doc** (especially online templates), verify the sample paths and module names against the actual repo layout. Lessons #2 and #3 came from copying templates whose paths were not real.
- **When excluding a directory from formatting**, list all hooks that touch that directory, not just the obvious one. `.prettierignore` doesn't exclude `pre-commit-hooks` built-ins. (Lesson #4.)
- **When two run-paths exist for the same tool** (here: `pre-commit run` from repo root vs `pnpm scripts` from `frontend/`), verify both run-paths against acceptance criteria. (Lesson #5.)
- **When the pre-existing `# noqa` violates a newly-introduced rule**, don't try to retrofit a per-line reason if the line is near the format limit. Use `[lint.per-file-ignores]` with a comment block explaining the pattern. (Lesson #6.)
