# Contributing to Lolday

Internal ISLab platform. The primary audiences are the lolday operator
(PO-LIN LAI) and Claude Code sessions.

## Setup

See the [README.md](README.md) Quick start; full procedure in
[docs/runbooks/deploy.md](docs/runbooks/deploy.md).

## Working in the repo

Branch / commit / PR conventions, spec & plan filenames, CI workflow:
[docs/conventions.md](docs/conventions.md).

Path-scoped rules (loaded automatically when Claude Code edits files in the area):

| Path                    | Rule file                             |
| ----------------------- | ------------------------------------- |
| `backend/**`            | `.claude/rules/backend.md`            |
| `frontend/**`           | `.claude/rules/frontend.md`           |
| `charts/**`             | `.claude/rules/charts-and-helm.md`    |
| `scripts/**`, `*.sh`    | `.claude/rules/scripts-and-ops.md`    |
| `backend/migrations/**` | `.claude/rules/alembic-migrations.md` |
| `.github/**`            | `.claude/rules/github-actions.md`     |

Hard rules that apply everywhere (SSH safety, sudo policy, China-origin software,
lint discipline, open-source-first, deploy-platform stance, MinIO-only storage):
[CLAUDE.md](CLAUDE.md).

## Testing

```bash
cd backend  && uv run pytest               # backend unit + integration
cd frontend && pnpm test                   # vitest unit
cd frontend && pnpm playwright test        # E2E (requires backend up; AUTH_DEV_MODE for local)
pre-commit run --all-files                 # lint + format whole repo
```

Per-area test patterns + which tests gate which CI workflow are documented in
the path-scoped rules above.

## Pull requests

PR title follows [Conventional Commits](https://www.conventionalcommits.org/)
and matches the squash-merge commit message. Body uses the template in
[.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md):

- **Summary** — what and why
- **Spec / Plan** — links to `docs/superpowers/specs|plans/` when non-trivial
- **Test plan** — bulleted checklist of what was tested

Squash-merge only. Lint + tests must be green. Full discipline:
[docs/conventions.md §3](docs/conventions.md).

## Filing issues

Use GitHub Issues for bugs and feature requests. Include:

- What you observed (logs, screenshots, `kubectl describe` output)
- What you expected
- Steps to reproduce (or link to the run / commit that exposed it)

Postmortems for incidents land in
[docs/postmortems/](docs/postmortems/) as `YYYY-MM-DD-<topic>.md`.
