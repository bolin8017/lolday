---
paths:
  - "backend/**/*.py"
  - "backend/pyproject.toml"
  - "backend/alembic.ini"
---

# Backend rules (FastAPI + uv)

## App structure

- Entry: `backend/app/main.py` (FastAPI app + lifespan + Prometheus instrumentator + router registration).
- `routers/` — one router per resource: admin, builds, cluster, credentials, datasets, detectors, experiments_proxy (MLflow proxy), internal, jobs, models_registry, users_me.
- `services/` — external integrations and business logic: build, cluster_status, crypto, dataset, discord (embed builders), events_tail, git, **gpu_signal** (host-aware GPU state via Prom + DCGM), harbor (init + main), job_config, jobs_params_validate, job_spec, job_tokens, k8s, manifest_store, mlflow_client, model_registry, notify (Discord HTTP delivery), rate_limit, validator.
- `models/` (SQLAlchemy 2.0 async ORM classes) and `schemas/` (Pydantic v2) are strictly separate. Keep DB types out of API responses.
- `auth/cf_access.py` is the only auth path. JWT is verified against Cloudflare Access JWKS.
- `deps.py` holds shared FastAPI `Depends(...)` factories: `current_active_user`, `require_role(...)`, `load_detector`, `require_detector_access`, `require_job_token`.
- `users.py` is a thin re-export — `from app.auth.cf_access import cf_access_user as current_active_user`. Do not add password-flow logic here.

## Startup fail-fast behaviour (onboarding trap)

Both checks run inside the FastAPI lifespan. A misconfigured deploy crashes the pod loud and early; this is by design.

- `_assert_schema_at_head()` raises `RuntimeError` if `alembic_version` in the DB does not match the `head` revision in `migrations/`. Forgetting `alembic upgrade head` is a CrashLoopBackOff, not a 500 at request time.
- `Settings.validate_sso_config` model_validator rejects boot when `ENVIRONMENT == "production"` and any of:
  - `AUTH_DEV_MODE=true` (dev bypass forbidden in prod)
  - `CF_ACCESS_TEAM_DOMAIN` empty
  - `CF_ACCESS_APP_AUD` empty

## Auth design

- Authentication is exclusively via `cf_access_user`. Neither `fastapi-users` nor `fastapi-users-db-sqlalchemy` is installed — the phase 7.5 baseline migration was rewritten to use SQLAlchemy 2.0 native `sa.Uuid()` directly. Do not add new auth backends, do not reintroduce `fastapi_users` imports.
- Every protected route uses `current_active_user` from `users.py`, which resolves to `cf_access_user`.
- `Role.SERVICE_TOKEN: -1` in `deps.py:ROLE_HIERARCHY` is intentional. A machine principal must always be less privileged than any human role; if it falls through to a `require_role(...)` guard, it gets a clean 403 instead of a `KeyError` 500. Do not raise this to 0+.

## Async DB

- SQLAlchemy 2.0 async + asyncpg in production.
- aiosqlite in tests via `backend/tests/conftest.py`.
- Session via `Depends(db.get_async_session)`. Do not create engines or sessions ad-hoc.

## Discord notify pattern

- `services/discord.py` builds embed dicts only. It does no HTTP and has no side effects.
- `services/notify.py` does the HTTP via httpx with a 5s timeout. It swallows all exceptions and increments `BACKEND_ERRORS{stage="discord_notify"}`.
- Callers wrap in `asyncio.create_task(notify_*(...))` for fire-and-forget. Do not `await` from a request handler. Do not add try/except around `notify_*` — exceptions are already handled.
- service-token-driven jobs skip notify (Phase 12). Do not "fix" this.
- The `deadmans-switch` CronJob uses an independent webhook (`DISCORD_URL` env, fail-fast on missing). Do not conflate the two.

## reconciler.py (57KB tech debt)

- Owns the Volcano vcjob → DB job sync, event-tail consumption, and orphan cleanup.
- Modify only when a corresponding phase spec covers the change (see `docs/superpowers/specs/2026-04-24-phase11b-*` and Phase 12 specs).
- Do not split the file unless a phase plan covers it.
- **New in Phase 6:** `app/reconciler/fifo_scheduler.py` — application-layer FIFO scheduler that pulls Job rows with `status=queued_backend` from DB every 30s, sorts by `(priority DESC, submitted_at ASC)`, and dispatches HEAD if `cluster.free_gpu >= job.gpu_count`; halts iteration if HEAD does not fit (strict FIFO, no leapfrog). Reads/writes `Job.status` and `Job.priority`. Runs as a separate asyncio task scheduled in the FastAPI lifespan; coexists with the existing reconciler (which syncs Volcano vcjob → DB state). The sync K8s pod-list call inside it is wrapped via `asyncio.to_thread` so a slow K8s API cannot block the event loop. See `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md` §6.4.

## maldet (external PyPI package)

- Pinned `maldet>=1.1,<2` in `pyproject.toml`. Detector logic lives in the maldet repo, not lolday.
- Bumping the pin requires reading the maldet CHANGELOG; the framework is what lolday integrates against, not extends.
- See `docs/superpowers/specs/2026-04-24-phase11-detector-framework-v1-design.md`.

## Tests

- `cd backend && uv run pytest`.
- `pytest-asyncio` runs in `asyncio_mode = "auto"`.
- MLflow is autouse-mocked. To opt out, mark the test `@pytest.mark.no_mock_mlflow`.
- Tests run against aiosqlite, not Postgres. Do not rely on Postgres-specific SQL (enums, JSONB operators, `RETURNING`-with-CTE) without testing on a Postgres dev DB too.

## Dependencies

- Add new deps via `uv add <pkg>`, never edit `pyproject.toml` by hand.
- Lock-step in production: `uv sync --frozen --no-dev --no-editable` in the Dockerfile.
- Do not write OIDC / JWT verification yourself — use fastapi-users / cf-access.
- Do not write retry logic yourself — use `httpx` + `tenacity` (or whatever is already in `pyproject.toml`).

## Lint / Format / Type-check 紀律

Tooling: **ruff** (lint + format) and **mypy** (type check). Config is at repo root: `ruff.toml` and `mypy.ini` — **do not** add `[tool.ruff]` or `[tool.mypy]` sections to `backend/pyproject.toml` (they would shadow the root config).

Manual commands from `backend/`:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
```

### Forbidden additions

- `black`, `flake8`, `pylint`, `isort`, `autopep8`, `yapf` — all replaced by ruff.
- Hand-edits to `pyproject.toml` for deps — use `uv add <pkg>` (existing rule).

### Rules

- Expanding `[lint] ignore` or `[lint.per-file-ignores]` to silence real errors is forbidden. To suppress a specific layout block, use `# fmt: off` / `# fmt: on` (ruff-supported, behaviour-equivalent to black) and add a brief reason comment.
- `# noqa: <code>` and `# type: ignore[<code>]` must be accompanied by a same-line reason (`# noqa: B008  # FastAPI Depends() pattern`). Bare suppressions are forbidden.
- mypy strictness is incrementally enabled: each `[mypy-<module>] ignore_errors = true` in `mypy.ini` is a tracked debt entry in `docs/architecture.md` §9. When a phase touches such a module, remove the override and fix types as part of that phase.

## Don't add

- New auth backends.
- New DB drivers.
- Mock-only tests for code that hits real services in prod (the test will pass but mask production drift).

## CI

Enforced by `.github/workflows/{lint,backend}.yml`. Discipline rules in `.claude/rules/github-actions.md`. Do not duplicate ruff / mypy invocations in `backend.yml` — `lint.yml` owns hygiene.
