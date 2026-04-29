# CLAUDE.md / docs Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current ad-hoc `CLAUDE.md` + scattered `docs/` layout with the structure defined in `docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md`: a slim `CLAUDE.md` index, path-scoped `.claude/rules/*.md`, a single comprehensive `docs/architecture.md`, conventions/runbooks docs, a phase-history archive, env-file examples, and clean auto-memory state.

**Architecture:** Documentation-only change. No code is touched. Work is split into 9 atomic commits so each can be reviewed/reverted independently. File creation tasks use detailed outlines (section headers + required content bullets); short files (env examples, postmortem placeholder, new CLAUDE.md, MEMORY.md) embed full content.

**Tech Stack:** Markdown, YAML frontmatter, `git mv`, `helm lint` (sanity).

---

## Pre-flight

- [ ] **Step 0.1: Confirm clean working tree on main**

```bash
git status
git rev-parse --abbrev-ref HEAD
```

Expected: working tree clean (or only `charts/lolday/helpers/build-helper/uv.lock` untracked, which is unrelated and should NOT be touched in this plan); branch `main`.

- [ ] **Step 0.2: Read the spec once end-to-end**

Read `docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md`. Every task below references specific spec sections.

- [ ] **Step 0.3: Capture pre-state for verification**

```bash
ls docs/                                             > /tmp/lolday-pre-docs.txt
ls -la                                               > /tmp/lolday-pre-root.txt
cat CLAUDE.md                                        > /tmp/lolday-pre-claude.md
ls ~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/ \
                                                     > /tmp/lolday-pre-memory.txt
```

These snapshots are used in Task 11's diff-based verification.

---

## Task 1: Create `.claude/rules/` (5 path-scoped rule files)

**Files:**

- Create: `.claude/rules/backend.md`
- Create: `.claude/rules/frontend.md`
- Create: `.claude/rules/charts-and-helm.md`
- Create: `.claude/rules/scripts-and-ops.md`
- Create: `.claude/rules/alembic-migrations.md`

**References:** spec §5 (each sub-section maps 1:1 to a file below).

- [ ] **Step 1.1: Create `.claude/rules/` directory**

```bash
mkdir -p .claude/rules
```

- [ ] **Step 1.2: Create `.claude/rules/backend.md`**

File header:

```markdown
---
paths:
  - "backend/**/*.py"
  - "backend/pyproject.toml"
  - "backend/alembic.ini"
---

# Backend rules (FastAPI + uv)
```

Body must contain these sections (each a `##` heading) with the listed bullets. Write prose around bullets.

1. **App structure** — `main.py` entry; routers (admin, builds, cluster, credentials, datasets, detectors, experiments_proxy, internal, jobs, models_registry, users_me); services; models (SQLAlchemy 2.0 async) vs schemas (Pydantic v2) — strictly separate; `auth/cf_access.py` for JWT verify; `deps.py` for shared FastAPI dependencies; `users.py` is a thin re-export of `cf_access_user as current_active_user`.
2. **Startup fail-fast behaviour (onboarding trap)** — `_assert_schema_at_head()` raises RuntimeError if alembic_version != head; `validate_sso_config` model_validator on `Settings` rejects boot when `ENVIRONMENT=production` AND (`AUTH_DEV_MODE=true` OR `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_APP_AUD` empty).
3. **Auth design** — fastapi-users is a thin shell; password flow stripped; everything goes through `cf_access_user`; `Role.SERVICE_TOKEN: -1` is intentional (machine principal less privileged than any human role) — never raise to 0+.
4. **Async DB** — SQLAlchemy 2.0 async + asyncpg in prod; aiosqlite in tests; sessions via `db.get_async_session`.
5. **Discord notify pattern** — caller wraps in `asyncio.create_task(notify_*(...))` (fire-and-forget); `notify.py` swallows exceptions and counts to Prom `BACKEND_ERRORS{stage="discord_notify"}`; never `await` it from a request path; service-token-triggered jobs skip notify (Phase 12).
6. **`reconciler.py` (57KB tech debt)** — owns Volcano vcjob ↔ DB sync + event tail + orphan cleanup. Modify only with a corresponding phase spec (phase11b/phase12). Do not split the file unless a phase plan covers it.
7. **`maldet` is an external PyPI package** — `maldet>=1.1,<2`. Detector logic lives in `maldet`, not lolday. See spec `2026-04-24-phase11-detector-framework-v1-design.md`.
8. **Tests** — `cd backend && uv run pytest`; `pytest-asyncio asyncio_mode = "auto"`; MLflow is autouse-mocked, opt out with `@pytest.mark.no_mock_mlflow`; aiosqlite for tests via conftest.
9. **Dependencies** — add via `uv add <pkg>`; do not edit `pyproject.toml` by hand; do not write OIDC/JWT yourself (use fastapi-users / cf-access); do not write retry yourself (httpx + tenacity if needed).
10. **Don't add** — new auth backends, new DB drivers, mock-only tests for code that hits real services in prod.

- [ ] **Step 1.3: Create `.claude/rules/frontend.md`**

Header:

```markdown
---
paths:
  - "frontend/**/*.{ts,tsx,js,jsx,css,json}"
---

# Frontend rules (Vite + React + TS)
```

Sections:

1. **Stack** — Vite 5, React 18, TS 5.5, Tailwind 3.4, shadcn/ui (Radix), react-router 7 file-based routing, TanStack Query v5, openapi-fetch + openapi-typescript, react-hook-form + zod, @rjsf for JSON-Schema forms, i18next zh-TW + en (zh-TW first-class), @tanstack/react-table, recharts.
2. **File-based routing rules** — `_authed.*` requires login; `$param` = path param; `_index` is the index page; layout for authed pages is `_authed.tsx`.
3. **API client convention** — every API call goes through `src/api/client.ts` (openapi-fetch); types come from `src/api/schema.gen.ts`; regenerate via `pnpm gen-api-types`; do not hand-write fetch/axios/swr.
4. **State convention** — server state → TanStack Query; URL state → react-router; form state → react-hook-form. Avoid global client state unless absolutely needed; do not introduce Redux.
5. **Component library discipline** — shadcn/ui first; check `components/ui/` before adding new primitives; do not introduce Ant Design / Naive UI / ElementUI / Arco / TDesign (China-origin).
6. **nginx CSP is strict (`script-src 'self'`)** — any inline `<script>` is blocked at runtime. No JSX `dangerouslySetInnerHTML` for executable content. Test in built container, not just dev mode.
7. **Tests** — `pnpm test` (vitest unit) + `pnpm playwright test` (E2E). Run `pnpm typecheck && pnpm lint` before commit.
8. **Duplicated config files (tech debt — DO NOT touch in this rule's scope)** — `playwright.config.{ts,js,d.ts}`, `vite.config.{ts,js,d.ts}`, `vitest.config.{ts,js,d.ts}`, `tailwind.config.{ts,js,d.ts}` each have three copies. Source of truth is `.ts`. The `.js` and `.d.ts` are accidental build emit committed in error; cleanup is in a follow-up phase.

- [ ] **Step 1.4: Create `.claude/rules/charts-and-helm.md`**

Header:

```markdown
---
paths:
  - "charts/**/*.{yaml,yml,tpl,json}"
  - "charts/**/Chart.lock"
---

# Helm chart rules (umbrella + sub-charts + helpers)
```

Sections:

1. **Umbrella structure** — `charts/lolday/Chart.yaml` (umbrella); `values.yaml` (~27KB, single source of truth); sub-charts shipped as tgz: `harbor 1.18.3`, `kube-prometheus-stack ~84.3.0` (alias `kps`), `loki ~7.0.0`, `alloy ~1.8.0`, `trivy-operator ~0.32.1`, `volcano ~1.14.1`. `Chart.yaml.appVersion` is currently `phase12` and lags real progress (follow-up phase will bump).
2. **Top-level templates** — backend.yaml, frontend.yaml, postgresql.yaml, redis.yaml, mlflow.yaml, registry.yaml, cloudflared.yaml, ingress.yaml, alembic-upgrade-hook.yaml (Helm pre-upgrade hook), volcano-queue.yaml, samples-pv/pvc, fernet/harbor/mlflow/cloudflared secrets, network policies (`network-policy.yaml`, `netpol-cloudflared.yaml`, `build-networkpolicy.yaml`, `job-networkpolicy.yaml`).
3. **`templates/monitoring/` subfolder** — alertmanager-config-discord, alertmanager-rules, deadmans-switch (CronJob, uses an independent `DISCORD_URL` env distinct from `DISCORD_WEBHOOK_URL_EVENTS`), grafana-admin-secret, grafana-dashboards, namespace, postgres-exporter (init-job + main), and ServiceMonitor × 6 (backend, dcgm, postgres, traefik, trivy, volcano).
4. **Helper images (`charts/lolday/helpers/`)** — `build-helper/` (Python, includes `maldet_validator.py` that asserts a built detector matches the maldet spec; has its own `pyproject.toml` + `uv.lock`), `job-helper/` (Python module + tests + `uv.lock`, runs as the vcjob entrypoint), `mlflow-server/` (Dockerfile only), `pytorch-cu12-base/` (Dockerfile only). Image versions are hardcoded in `backend/app/config.py` (`BUILD_IMAGE_HELPER=v3`, `JOB_HELPER_IMAGE=v4`).
5. **Dashboards (`charts/lolday/dashboards/`)** — dcgm.json, postgresql.json, reconciler-errors.json, traefik.json, trivy-security.json. Mounted by `monitoring/grafana-dashboards.yaml`.
6. **Workflow** — `helm lint charts/lolday`; `helm template charts/lolday > /tmp/out.yaml` to inspect rendered diff; `helm dependency update charts/lolday` re-fetches sub-chart tgz; never commit `*.tgz` (already in `.gitignore`).
7. **NetworkPolicy changes** — read the SSH safety hard rule in root `CLAUDE.md` first. Any iptables-affecting change on server30 must be dry-run-able.
8. **values.yaml** — single file today (no dev/prod overlay); secrets go through `*-secret.yaml` templates wired to external sources, never plaintext in values.yaml.

- [ ] **Step 1.5: Create `.claude/rules/scripts-and-ops.md`**

Header:

```markdown
---
paths:
  - "scripts/**"
  - "*.sh"
---

# Scripts & ops rules
```

Sections:

1. **Script categories** — install/deploy (`install-tools.sh`, `setup-k3s.sh`, `deploy.sh`, `teardown.sh`); diagnostics (`diag-backend-401.sh`, `diag-pv-data.sh`, `disk-diag.sh`, `find-lost-data.sh`); recovery (`recover-harbor.sh`, `harbor-inventory.sh`, `fix-lolday-project-public.sh`, `patch-k3s-registries.sh`); data migration (`migrate-ephemeral-to-ssd.sh`, `migrate-all-root-pvcs.sh`, `cleanup-migrated-shelves.sh`); phase pre-checks (`phase4-pre-deploy-check.sh`, `phase6-pre-deploy-check.sh`); one-shot Python (`backfill-summary-metrics.py`, `sample_elf_dataset.py`).
2. **Sudo discipline** — operator has no sudo by default; sudo is granted temporarily and revoked. Never run `set -euo pipefail` and then sudo the whole script. Wrap individual sudo lines, comment `# requires sudo`, and echo a banner if the script needs sudo end-to-end.
3. **SSH discipline (covered by hard rule, with operational specifics)** — for any iptables / ufw / cilium / k3s flannel / sysctl change, dry-run the plan to stdout, then prompt the operator to verify SSH from a fresh session before applying.
4. **Secrets path fallback pattern** — new and updated scripts must follow `recover-harbor.sh` / `harbor-inventory.sh`'s pattern:

   ```bash
   REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
   SECRETS=${SECRETS:-${REPO_ROOT}/.lolday-secrets.env}
   [ -f "$SECRETS" ] || SECRETS="$HOME/.lolday-secrets.env"
   ```

   Currently `fix-lolday-project-public.sh`, `phase6-pre-deploy-check.sh`, and others still hardcode `~/.lolday-secrets.env`. Fixing them is a follow-up phase (see spec §14).

5. **Writing a new script** — `#!/usr/bin/env bash`, `set -euo pipefail`, `${VAR:?required}` for mandatory env vars, `[step N] ...` echo-format logs, errors to stderr.
6. **Phase pre-deploy checks** — `phase4-pre-deploy-check.sh` and `phase6-pre-deploy-check.sh` are templates; new phases that touch deploy should add an analogous pre-check.

- [ ] **Step 1.6: Create `.claude/rules/alembic-migrations.md`**

Header:

```markdown
---
paths:
  - "backend/migrations/**"
---

# Alembic migration rules
```

Sections:

1. **Filename convention** — `<rev>_phase<N>(_<minor>)_<short_desc>.py`. Generate via `alembic revision -m "phaseN_X_<desc>"`, then rename the prefix to keep history readable.
2. **Existing phase mapping (do not rename)** — list the 10 current migrations:
   - `d3f179666394_phase7_5_baseline.py`
   - `8a1c2d4e5f60_phase8_gpu2_profile.py`
   - `b2e7c8a1f330_phase10_sso_admin_email.py`
   - `74c95d81f74e_phase11b_events_manifest.py`
   - `12f13a2e3d68_phase11c_drop_v0_schema_columns.py`
   - `c7e3a9b1d042_phase12_1_service_token_friendly_name.py`
   - `f9a2c4e8b01a_phase12_2_role_service_token.py`
   - `a4b8e7c91d52_phase12_3_role_enum_lowercase.py`
   - `f91615e44fad_phase13a_detector_version_deleted_enum.py`
   - `f37230063a20_phase13b_job_user_params_column.py`
3. **Workflow** — `cd backend && uv run alembic revision --autogenerate -m "phaseN_X_<desc>"`; manually review (autogenerate is unreliable for enums/indexes/server_default); `uv run alembic upgrade head` against a dev DB; never run downgrade in prod — roll forward with a reverse migration.
4. **Enum gotchas (real history)** — phase12.1 / 12.2 / 12.3 are three sequential patches against a single role_enum. Cause: SQLAlchemy enum + Postgres ENUM type + lowercase value mismatch + missing `values_callable`. See `docs/phase-history/phase12.1-role-enum-bug.md`.
5. **NOT NULL columns** — must ship with `server_default` or be split into a 2-step migration (add nullable + backfill + alter to NOT NULL).
6. **Schema head check is enforced at backend boot** — `_assert_schema_at_head()` raises if `alembic_version != head`. Forgetting `alembic upgrade head` is a CrashLoopBackOff, not a silent 500.

- [ ] **Step 1.7: Verify all 5 rule files have `paths:` frontmatter**

```bash
for f in .claude/rules/*.md; do
  echo "=== $f ==="
  head -5 "$f"
done
```

Expected: each file starts with `---\npaths:\n  - "..."\n---`.

- [ ] **Step 1.8: Verify line counts within budget (60–150 each)**

```bash
wc -l .claude/rules/*.md
```

Expected: each file 60–150 lines.

- [ ] **Step 1.9: Commit**

```bash
git add .claude/rules/
git commit -m "$(cat <<'EOF'
docs(rules): add path-scoped .claude/rules for backend, frontend, charts, scripts, migrations

Adds the path-scoped rule files defined in
docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md §5.
Each file uses YAML frontmatter `paths:` so it loads only when Claude
reads matching files, leaving the base context light.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `docs/architecture.md`

**Files:**

- Create: `docs/architecture.md`

**References:** spec §6 (10 chapters).

- [ ] **Step 2.1: Create file with chapter skeleton**

Top of file:

```markdown
# Lolday Architecture

> Target audience: engineers / AI sessions new to lolday. After reading this
> document you should be able to describe what each component does, how data
> flows, what external services we depend on, where env vars live, and which
> traps to avoid.

> Source spec: docs/superpowers/specs/2026-03-30-lolday-platform-design.md
> (original) and the per-phase specs under docs/superpowers/specs/.
```

Then 10 `##` chapters, in order:

1. Purpose & positioning
2. System diagram
3. Component responsibility table
4. Data flows
5. Env vars & config sources
6. Build / Test / Release
7. External dependencies
8. Phase progression
9. Known tech debt
10. Common gotchas

- [ ] **Step 2.2: Fill Chapter 1 — Purpose & positioning (~30 lines)**

Required content:

- Lolday = ISLab internal ML platform managing **malware detector lifecycle**.
- It is **not** an ML framework. Detector logic lives in the external `maldet` PyPI package; lolday is glue.
- Target deploy: **server30** single-node K3s. Ubuntu 24.04, shared lab.
- Non-goals: multi-tenant SaaS, multi-cluster, cloud-managed.

- [ ] **Step 2.3: Fill Chapter 2 — System diagram (~40 lines)**

Render with mermaid `C4Container` syntax. Required nodes & edges:

- Browser → Cloudflare tunnel (cloudflared) → Traefik ingress → frontend (nginx) / backend (FastAPI).
- backend → PostgreSQL, Redis, MLflow tracking server, Harbor registry, K8s API (Volcano vcjob).
- Volcano vcjob → job-helper image → maldet detector → write MLflow run + push image to Harbor.
- Loki ← stdout via Alloy. Prometheus ← ServiceMonitor × 6. Grafana ← Prom + Loki. Alertmanager ← Prom rules → Discord webhook.
- Trivy operator → Harbor → vuln reports.
- deadmans-switch CronJob → independent Discord webhook (`DISCORD_URL`).

- [ ] **Step 2.4: Fill Chapter 3 — Component responsibility table (~80 lines)**

Markdown table, columns: `元件 | 技術 | 進入點 | 主要責任 | 對應 rules / specs`.

Rows (group by section):

Platform:

- backend / FastAPI 0.115 + Py3.12 / `backend/app/main.py` / REST API + reconciler / `rules/backend.md`
- frontend / Vite + React 18 + TS 5.5 / `frontend/src/main.tsx` / UI; pull API via TanStack Query / `rules/frontend.md`
- reconciler / in-process within backend / `backend/app/reconciler.py` / watch vcjob events; sync DB / phase11b/12 specs
- Volcano queue / volcano 1.14.1 / `charts/lolday/templates/volcano-queue.yaml` / GPU batch scheduling / `rules/charts-and-helm.md`
- Harbor / sub-chart 1.18.3 / `charts/lolday/charts/harbor-*.tgz` / OCI registry for detector images / `scripts/recover-harbor.sh`
- MLflow / mlflow-skinny 2.20 + custom server image / `charts/lolday/helpers/mlflow-server/` / experiment tracking + model registry / `services/mlflow_client.py`
- PostgreSQL / bitnami sub-chart / `templates/postgresql.yaml` / primary DB / `backend/migrations/`
- Redis / bitnami sub-chart / `templates/redis.yaml` / rate-limit, event-tail buffer / `services/rate_limit.py`
- Cloudflared / `templates/cloudflared.yaml` / SSO tunnel / `auth/cf_access.py`
- kube-prometheus-stack / 84.3.0 sub-chart / — / Prom + Grafana + Alertmanager / `templates/monitoring/`
- Loki + Alloy / 7.0.0 + 1.8.0 / — / log aggregation / —
- Trivy operator / 0.32.1 / — / image vuln scan / —
- GPU operator / upstream chart (NOT in this repo) / installed via README setup / NVIDIA driver + DCGM exporter

Helpers (`charts/lolday/helpers/`):

- build-helper / Py / `maldet_validator.py` / validate built detector matches maldet spec / `rules/charts-and-helm.md`
- job-helper / Py module / `job_helper/` / vcjob entrypoint / `rules/charts-and-helm.md`
- mlflow-server / Dockerfile only / — / custom mlflow image / —
- pytorch-cu12-base / Dockerfile only / — / GPU base image / —

Monitoring (`templates/monitoring/`):

- alertmanager rules + Discord receiver / `alertmanager-rules.yaml`, `alertmanager-config-discord.yaml`
- deadmans-switch CronJob / `deadmans-switch.yaml` + `files/deadmans_switch/check.py` / fail-fast on missing `DISCORD_URL`
- postgres-exporter (init job + main) / `postgres-exporter*.yaml`
- ServiceMonitor × 6 / `servicemonitor-{backend,dcgm,postgres,traefik,trivy,volcano}.yaml`
- Grafana dashboards / `grafana-dashboards.yaml` + `dashboards/*.json`

Notifications:

- Discord events webhook / `services/discord.py` (embed builders) + `services/notify.py` (HTTP delivery, fire-and-forget)
- deadmans-switch / independent webhook via `DISCORD_URL`

- [ ] **Step 2.5: Fill Chapter 4 — Data flows (~60 lines)**

Five subsections:

- **4.1 Build a detector** — user → frontend → `POST /detectors` → DB row → backend triggers build via `build-helper` image (BuildKit) → push to Harbor → mark ready.
- **4.2 Run a job (core flow)** — user → `POST /jobs` → backend writes DB row + creates Volcano vcjob → vcjob pulls detector image + dataset PVC → runs → writes MLflow run → emits events → reconciler syncs DB.
- **4.3 SSO / auth** — browser → Cloudflare Access → `CF-Access-Jwt-Assertion` header → `cf_access.py` JWKS verify → `users_me` get-or-creates DB User.
- **4.4 Monitoring & logs** — backend prometheus-fastapi-instrumentator → ServiceMonitor → Prom; stdout → Alloy → Loki; Grafana dashboards via `monitoring/grafana-dashboards.yaml`.
- **4.5 Notifications (fire-and-forget)** — caller (reconciler / build pipeline) wraps `asyncio.create_task(notify_*(...))`; `notify.py` swallows exceptions and counts to `BACKEND_ERRORS{stage="discord_notify"}`. To debug a missing notification, inspect the Prom counter, not the caller. service-token-driven jobs skip notify (Phase 12). deadmans-switch is a separate channel via its own webhook; missing config = CrashLoopBackOff (intentional).

- [ ] **Step 2.6: Fill Chapter 5 — Env vars & config (~40 lines)**

Two subsections:

**5.1 Runtime env vars (read by backend, set via Helm `values.yaml`)** — table form. Reference `backend/app/config.py` as authoritative. Group:

- Core: `DATABASE_URL`, `REDIS_URL`, `DOCS_ENABLED`, `ENVIRONMENT`, `LOLDAY_UI_BASE_URL`
- Crypto: `FERNET_KEY`
- Harbor: `HARBOR_URL`, `HARBOR_ADMIN_USERNAME/PASSWORD`, `HARBOR_IMAGE_PREFIX`
- Build: `BUILD_NAMESPACE`, `BUILD_IMAGE_HELPER`, `BUILD_IMAGE_BUILDKIT`, `BUILD_IMAGE_GIT`, `BUILD_TIMEOUT_SECONDS`, `BUILD_CONCURRENCY_PER_USER`, `BUILD_LOG_TAIL_BYTES`, `REPO_MAX_SIZE_MB`
- Backend self-URL: `BACKEND_INTERNAL_URL`, `INTERNAL_EVENTS_BASE_URL`
- Reconciler: `RECONCILER_ENABLED`
- Job: `JOB_NAMESPACE`, `JOB_HELPER_IMAGE`, `JOB_ACTIVE_DEADLINE_*_SECONDS` (3), `JOB_TTL_SECONDS_AFTER_FINISHED`, `JOB_NODE_SELECTOR_HOSTNAME`, `JOB_PER_USER_CONCURRENCY`, `JOB_IDEMPOTENCY_WINDOW_SECONDS`, `JOB_BACKEND_URL`
- MLflow: `MLFLOW_TRACKING_URI`, `MLFLOW_HTTP_TIMEOUT_SECONDS`, `MLFLOW_HTTP_RETRIES`
- Dataset: `DATASET_CSV_MAX_BYTES`, `DATASET_SPOT_CHECK_COUNT`, `DATASET_SPOT_CHECK_MISSING_THRESHOLD`, `SAMPLES_ROOT`, `SAMPLES_LOCAL_ROOT`
- Discord: `DISCORD_WEBHOOK_URL_EVENTS`, `DISCORD_HTTP_TIMEOUT_SECONDS`
- Cloudflare Access SSO: `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_APP_AUD`, `CF_ACCESS_JWKS_CACHE_TTL_SECONDS`, `AUTH_DEV_MODE`, `AUTH_DEV_EMAIL`

State that `backend/app/config.py` is the single source of truth; this section is a navigational summary.

**5.2 Operator-local env files (repo root, gitignored)** — table:

- `.lolday-secrets.env` — chmod 600, sourced by `scripts/deploy.sh`, `recover-harbor.sh`, `harbor-inventory.sh`, `fix-lolday-project-public.sh`, `diag-backend-401.sh`, `phase6-pre-deploy-check.sh`. Contains: `GRAFANA_ADMIN_PASSWORD`, `PG_EXPORTER_PASSWORD`, `CF_ENABLED`, `CF_TUNNEL_TOKEN`, `DISCORD_WEBHOOK_URL_EVENTS`, `HARBOR_ADMIN_PASSWORD`, `FERNET_KEY`, plus other operator-managed values.
- `.lolday-cf-svctoken.env` — chmod 600, used to test svctoken auth via `/users/me` (see `docs/phase-history/phase12.1-role-enum-bug.md`).
- `.lolday-cloudflare-access-backups/` — JSON snapshots of Cloudflare Access app/policy state (audit backups).

**5.3 Known inconsistency (tracked, not fixed here)** — `config.py` uses three Harbor URL forms: `harbor.harbor.svc.cluster.local:80` (`HARBOR_URL`), `harbor.harbor.svc:80` (`HARBOR_IMAGE_PREFIX`), `harbor.lolday.svc:80` (`JOB_HELPER_IMAGE`). The third looks like a typo. Listed in §9.

- [ ] **Step 2.7: Fill Chapter 6 — Build / Test / Release (~40 lines)**

Required points:

- **No GitHub Actions.** No automated CI. All build / test / release happens locally then via `scripts/deploy.sh`. (Listed in §9.)
- **Backend image** — `backend/Dockerfile`: `python:3.12-slim` + uv installed via copy from `ghcr.io/astral-sh/uv:latest`; `uv sync --frozen --no-dev --no-editable`; CMD runs uvicorn.
- **Frontend image** — `frontend/Dockerfile`: 2-stage. Build with `node:22-alpine` + corepack + pnpm. Serve with `nginxinc/nginx-unprivileged:1.27-alpine` (non-root, listens 8080, supports `readOnlyRootFilesystem`). HEALTHCHECK on `/healthz`.
- **Helper images** — `charts/lolday/helpers/{build-helper,job-helper,mlflow-server,pytorch-cu12-base}/` each have a Dockerfile. **Built and pushed manually by operator.** Versions are hardcoded in `backend/app/config.py` (`:v3`, `:v4`).
- **Backend tests** — `cd backend && uv run pytest`. Async via pytest-asyncio. MLflow autouse-mocked.
- **Frontend tests** — `cd frontend && pnpm test` (vitest unit) + `pnpm playwright test` (E2E). Plus `pnpm typecheck && pnpm lint` before commit.
- **Repo-level tests** — `tests/phase7/` is a directory of shell-based integration tests (alertmanager, volcano queue, ServiceMonitor smoke). Not run automatically.
- **Release** — `bash scripts/deploy.sh` (no sudo). Internally: `helm dependency update` then `helm upgrade --install`. Migrations run via `templates/alembic-upgrade-hook.yaml` (Helm pre-upgrade hook Job).

- [ ] **Step 2.8: Fill Chapter 7 — External dependencies (~30 lines)**

- **Cloudflare Access** — SSO; JWKS at `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`; backend verifies JWT in `auth/cf_access.py`. Production boot fails if team domain or AUD is empty.
- **Cloudflare Tunnel (cloudflared)** — exposes the cluster to the public internet; token in `.lolday-secrets.env` as `CF_TUNNEL_TOKEN`.
- **Discord webhooks (× 2)** — events (`DISCORD_WEBHOOK_URL_EVENTS`, set on backend Deployment via Helm) + deadmans-switch (`DISCORD_URL`, set on the CronJob env).
- **GitHub** — code host. No Actions configured.
- **maldet (PyPI)** — external detector framework. Pin: `maldet>=1.1,<2`. Bump only after reading the maldet repo CHANGELOG.
- **NVIDIA GPU operator** — installed via upstream Helm chart (NOT this repo's chart). DCGM exporter feeds Prom.

- [ ] **Step 2.9: Fill Chapter 8 — Phase progression (~30 lines)**

Table or bulleted list mapping phase → spec/plan filename(s) → one-line description. Cover phases 1, 2, 3, 4, 5, 6, 7/7.5, 8, 9.6, 10, 11a–e, 12.x, 13a, 13b. Use spec/plan filenames from `docs/superpowers/specs/` and `docs/superpowers/plans/`. Note: Phase 9.6 has no spec/plan in the repo (operational migration), only `docs/phase-history/migrate*.sh` references and Phase 9.6 PVC findings (referenced in old auto-memory).

End the chapter with: "Operational checklists & retrospective findings: `docs/phase-history/`."

- [ ] **Step 2.10: Fill Chapter 9 — Known tech debt (~40 lines)**

Bulleted list:

1. `backend/app/reconciler.py` (57KB) — single-file beast. Refactor only with phase plan.
2. **No CI/CD.** No GitHub Actions, no automated build/test, no release pipeline. `scripts/deploy.sh` is manual.
3. **Single `values.yaml`** (~27KB). No dev/prod overlay.
4. **Helper images built by hand.** No automated build/push of `build-helper:vN` / `job-helper:vN`.
5. **Frontend has duplicated config files.** `playwright/vite/vitest/tailwind.config` each has `.ts`, `.js`, `.d.ts`. Source of truth is `.ts`. The `.js`/`.d.ts` are accidental build emit committed in error.
6. **`frontend/tsconfig.node.tsbuildinfo`** (52KB) is committed; should be in `.gitignore`.
7. **No pre-commit / husky / lint-staged / prettier / `.editorconfig`.** No automated formatting discipline.
8. **No `[tool.ruff]` / `[tool.mypy]` config in `backend/pyproject.toml`.** Caches exist but settings are default.
9. **`charts/lolday/Chart.yaml` `appVersion: "phase12"`** lags real progress (currently phase 13b).
10. **`README.md` link** `docs/superpowers/plans/2026-04-13-phase1-infrastructure-v2.md` is broken (real file is `2026-03-30-phase1-infrastructure.md`). Fixed in the same PR as this restructure.
11. **fastapi-users vestige** — `User.hashed_password` column still present but unused since Phase 10 SSO migration.
12. **Helper image versions hardcoded** — `BUILD_IMAGE_HELPER=v3`, `JOB_HELPER_IMAGE=v4` in `config.py`. No versioning strategy.
13. **Secrets path inconsistency** — 4 scripts hardcode `~/.lolday-secrets.env`; should follow the fallback pattern (`recover-harbor.sh` is the model).
14. **Harbor URL inconsistency** — three forms in `config.py`. `harbor.lolday.svc:80` looks like a typo for `harbor.harbor.svc:80`.

- [ ] **Step 2.11: Fill Chapter 10 — Common gotchas (~30 lines)**

Bulleted list:

1. **SSH on server30** — see hard rule. Cilium 2026-03-31 incident.
2. **Alembic autogenerate is unreliable** for enums, indexes, server_default. Phase 12.1 / 12.2 / 12.3 are the receipts.
3. **Helm `dependency update`** re-fetches sub-chart tgz files; never commit them.
4. **Harbor reinstall resets robot creds.** Use `scripts/recover-harbor.sh`.
5. **maldet bump** — read the external repo's CHANGELOG before raising the pin.
6. **MLflow tests are autouse-mocked.** Reverse the marker (`@pytest.mark.no_mock_mlflow`) for tests that must hit a real server.
7. **Schema head check is fail-fast on boot** — forgetting `alembic upgrade head` produces RuntimeError, not 500.
8. **`AUTH_DEV_MODE=true` in production is rejected at boot.** Intentional.
9. **CSP `'self'` only** — any inline script in the SPA is blocked at runtime.
10. **`lolday_volcano_pending_stale` Gauge** triggers an alert when Volcano hasn't scheduled a Pending job within `VOLCANO_STALE_SECONDS`. Looks like a backend bug; isn't.
11. **service-token jobs skip Discord notify.** Don't try to "fix" this — it's intentional (Phase 12).
12. **`Role.SERVICE_TOKEN: -1`** is an intentional negative weight; do not raise.

- [ ] **Step 2.12: Verify chapter coverage**

```bash
grep -c "^## " docs/architecture.md
```

Expected: 10 (one `##` per chapter).

- [ ] **Step 2.13: Verify line count within budget (350–500)**

```bash
wc -l docs/architecture.md
```

Expected: 350–500.

- [ ] **Step 2.14: Verify mermaid block present**

````bash
grep -c '```mermaid' docs/architecture.md
````

Expected: ≥ 1.

- [ ] **Step 2.15: Commit**

```bash
git add docs/architecture.md
git commit -m "$(cat <<'EOF'
docs: add architecture.md (single-file system architecture)

Onboarding-grade architecture document covering purpose, system diagram,
component responsibility table, data flows (incl. fire-and-forget Discord
notify), env vars (runtime + operator-local), build/test/release, external
deps, phase progression, known tech debt, and gotchas.

Per spec §6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Create `docs/conventions.md`

**Files:**

- Create: `docs/conventions.md`

**References:** spec §7.

- [ ] **Step 3.1: Create file with header**

```markdown
# Conventions

> Source spec: docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md §7.
> Effective from 2026-04-29. Pre-existing commits are NOT rewritten.
```

- [ ] **Step 3.2: Section "1. Branch naming" — full content**

```markdown
## 1. Branch naming (mainstream)

`<type>/<short-kebab-desc>`

Examples:

- `feat/job-detail-tabs`
- `fix/role-enum-lowercase`
- `chore/bump-deps`
- `docs/restructure-claude-md`
- `refactor/reconciler-split`

Allowed types: `feat | fix | chore | docs | refactor | test | perf | build | ci`.
```

- [ ] **Step 3.3: Section "2. Commit messages — Conventional Commits" — full content**

```markdown
## 2. Commit messages — Conventional Commits

Format: `<type>(<scope>): <subject>`

Examples:

- `feat(jobs): add detail summary tab`
- `fix(auth): align role_enum to values_callable`
- `chore(charts): bump kube-prometheus-stack to 84.4.0`

Rules:

- `scope` is a module name (`jobs`, `auth`, `reconciler`, `harbor`, `charts`,
  `frontend`, `backend`, `migrations`, `rules`, `docs`). It is NOT a phase number.
- `subject` is imperative, lowercase, no trailing period.
- Body is optional but encouraged for non-trivial changes; wrap at 72.
- Footer for `Co-Authored-By:` and `Closes #N`.

Multi-commit branches: each commit follows the format. PR title = the most
representative commit's message (squash-merge friendly).
```

- [ ] **Step 3.4: Section "3. Pull requests" — full content**

```markdown
## 3. Pull requests

PR title format: same as a Conventional Commit.

PR description must include the spec/plan link when one exists:
```

Spec: docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md
Plan: docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md

```

PRs without a spec are acceptable for hotfixes and tiny doc fixes.
```

- [ ] **Step 3.5: Section "4. Phase numbering — only in planning docs" — full content**

```markdown
## 4. Phase numbering — only in planning docs

Phase numbers (`phaseN-X`) live in:

- `docs/superpowers/specs/YYYY-MM-DD-phaseN-X-design.md`
- `docs/superpowers/plans/YYYY-MM-DD-phaseN-X.md`
- PR descriptions (as `Spec:` / `Plan:` pointers)

They do NOT appear in branch names, commit subjects, or commit scopes.

Hotfixes that don't belong to a phase use `fix/<short-desc>` and (if
post-mortem-worthy) get a `docs/postmortems/YYYY-MM-DD-<topic>.md`. Never
invent sub-phases like `phase12.1.1` to host a hotfix.
```

- [ ] **Step 3.6: Section "5. Cut-over (2026-04-29)" — full content**

```markdown
## 5. Cut-over

These conventions apply from 2026-04-29 forward. Pre-existing commits and
branches keep their original form; we don't rewrite history.
```

- [ ] **Step 3.7: Section "6. Migration filename convention" — pointer**

```markdown
## 6. Migration filename convention

See `.claude/rules/alembic-migrations.md`.
```

- [ ] **Step 3.8: Section "7. Code naming" — full content**

```markdown
## 7. Code naming

- Python: `snake_case` for functions/vars, `PascalCase` for classes, `UPPER_SNAKE` for module constants.
- Kubernetes resources / Helm values keys: `kebab-case`.
- TypeScript / React components: `camelCase` for vars, `PascalCase` for components, `kebab-case` for filenames.
```

- [ ] **Step 3.9: Section "8. Three test layers" — full content**

```markdown
## 8. Three test layers

- `backend/tests/` — pytest (unit, service, reconciler, migrations). Run: `cd backend && uv run pytest`.
- `frontend/tests/unit/` — vitest. Run: `cd frontend && pnpm test`.
- `frontend/tests/e2e/` — playwright. Run: `cd frontend && pnpm playwright test`.
- `tests/phase7/` — shell-based integration smokes (alertmanager, volcano queue, ServiceMonitor presence). Run individually; not gated by anything.
```

- [ ] **Step 3.10: Section "9. Before writing new code" — full content**

```markdown
## 9. Before writing new code

Read the path-scoped rule for the area you're touching:

- `backend/...` → `.claude/rules/backend.md`
- `frontend/...` → `.claude/rules/frontend.md`
- `charts/...` → `.claude/rules/charts-and-helm.md`
- `scripts/...` → `.claude/rules/scripts-and-ops.md`
- `backend/migrations/...` → `.claude/rules/alembic-migrations.md`
```

- [ ] **Step 3.11: Verify line count**

```bash
wc -l docs/conventions.md
```

Expected: 80–150.

- [ ] **Step 3.12: Commit**

```bash
git add docs/conventions.md
git commit -m "$(cat <<'EOF'
docs: add conventions (GitHub Flow + Conventional Commits)

Adopts mainstream OSS conventions: branch <type>/<short-kebab-desc>,
Conventional Commits with module scope, phase numbering retained only
in specs/plans/PR descriptions. Cut-over 2026-04-29; pre-existing
commits unchanged.

Per spec §7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Create `docs/runbooks/{deploy,troubleshooting}.md`

**Files:**

- Create: `docs/runbooks/deploy.md`
- Create: `docs/runbooks/troubleshooting.md`

**References:** spec §8.

- [ ] **Step 4.1: Create directory and `deploy.md` skeleton**

```bash
mkdir -p docs/runbooks
```

`docs/runbooks/deploy.md` header:

```markdown
# Deploy runbook (server30, K3s, Helm)

> Source: merges `docs/phase-history/phase3-deploy-runbook.md` and
> `docs/phase-history/host-prep.md`. The originals stay in phase-history
> for traceability; this is the live runbook.
```

Sections (all `##`):

1. Pre-requisites
2. K3s install
3. GPU operator install
4. Cloudflare Access App + tunnel setup
5. `bash scripts/deploy.sh`
6. Alembic upgrade hook (automatic)
7. Verification checklist
8. Rollback

- [ ] **Step 4.2: Fill `deploy.md` Section 1 — Pre-requisites**

Required content (copied / paraphrased from `docs/phase-history/host-prep.md`):

- Ubuntu 24.04 host with NVIDIA driver installed (`nvidia-smi` works on host).
- Operator account with **temporary** sudo access (will be revoked).
- Tools: `bash scripts/install-tools.sh` installs kubectl, helm, k9s, etc. into `~/.local/bin/` (no sudo).
- **Create operator-local secret files** from examples:

  ```bash
  cp .lolday-secrets.env.example .lolday-secrets.env
  chmod 600 .lolday-secrets.env
  # fill in values

  cp .lolday-cf-svctoken.env.example .lolday-cf-svctoken.env
  chmod 600 .lolday-cf-svctoken.env
  # fill in CF service token
  ```

- Confirm SSH stays alive on port 9453 throughout deploy (see hard rule).

- [ ] **Step 4.3: Fill `deploy.md` Sections 2–8**

Reuse content from `docs/phase-history/phase3-deploy-runbook.md` (after the move in Task 7) and `host-prep.md`. Each section concrete:

2. **K3s install** — `sudo bash scripts/setup-k3s.sh` (give to sudo-capable account); verify `kubectl get nodes`.
3. **GPU operator** — README's `helm install gpu-operator nvidia/gpu-operator ... --wait --timeout 5m` block, verbatim.
4. **Cloudflare** — get tunnel token from CF dashboard → `.lolday-secrets.env` `CF_TUNNEL_TOKEN`. Configure Access App with desired domain; record `CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_APP_AUD` in Helm `values.yaml` overrides.
5. **`bash scripts/deploy.sh`** — runs `helm dependency update charts/lolday`, then `helm upgrade --install lolday charts/lolday -n lolday`. Emit instructions to inspect with `kubectl get pods -n lolday -w`.
6. **Alembic upgrade hook** — `templates/alembic-upgrade-hook.yaml` runs as Helm `pre-upgrade` Job. Verify `kubectl get jobs -n lolday | grep alembic-upgrade`. Backend pod's `_assert_schema_at_head()` will RuntimeError if this hook didn't reach `alembic_version = head`.
7. **Verification checklist** — list:
   - `kubectl get pods -n lolday` all Running
   - `kubectl get vc -n lolday` (volcano queue exists)
   - `curl -k https://<lolday-domain>/healthz`
   - `kubectl get servicemonitor -n monitoring` shows backend, dcgm, postgres, traefik, trivy, volcano
   - Grafana reachable; default dashboards present
   - Discord events webhook works: trigger a small job and confirm notification
   - Deadmans-switch CronJob ran at least once
8. **Rollback** — `helm rollback lolday <prev-rev>`. For schema, write a new forward migration that reverses the change; never run `alembic downgrade` in prod.

- [ ] **Step 4.4: Create `docs/runbooks/troubleshooting.md`**

Header:

```markdown
# Troubleshooting (symptom → action)

> Source: ad-hoc consolidation of scripts/diag-_, scripts/recover-_, and
> known incident patterns. Symptom-keyed for fast lookup.
```

Body: a markdown table or sectioned list, each entry: `### Symptom: <brief>` then `**Cause hypothesis:**` and `**Action:**`. Required entries:

1. **Backend 401 on every request / can't log in** — check `cf_access` env (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_APP_AUD`); run `bash scripts/diag-backend-401.sh`; confirm CF tunnel up.
2. **Backend pod CrashLoopBackOff with "DB schema mismatch"** — alembic upgrade hook didn't run or rolled back. Inspect `kubectl logs job/<lolday-alembic-upgrade-...>`. Re-run hook: `helm upgrade --install ...` re-creates the Job.
3. **Backend pod CrashLoopBackOff with "AUTH_DEV_MODE=true is forbidden when ENVIRONMENT=production"** — someone overrode env for local dev. Set `ENVIRONMENT=development` or remove the override.
4. **Image pull 401 on K3s** — `bash scripts/patch-k3s-registries.sh` then `bash scripts/fix-lolday-project-public.sh`. Often paired with Harbor robot creds reset; re-run `bash scripts/recover-harbor.sh`.
5. **PV data appears missing** — `bash scripts/diag-pv-data.sh` then `bash scripts/find-lost-data.sh`. Phase 9.6 incident notes in `docs/phase-history/`.
6. **Disk full / `/` full** — `bash scripts/disk-diag.sh`. Phase 8.2 / 9.6 migration scripts move PVCs off root LV.
7. **Volcano scheduling stalled / `lolday_volcano_pending_stale` alert** — `kubectl get vc -n lolday`, `kubectl describe vcjob`. Often Volcano controller pod crashed.
8. **Discord notifications missing** — Prom counter `BACKEND_ERRORS{stage="discord_notify"}` shows delivery failures; check `kubectl get secret -n lolday discord-events` and webhook env on backend Deployment. Notifications are fire-and-forget — silence in code is by design.
9. **CSP blocks loaded script** — frontend nginx CSP is `script-src 'self'` only. Move inline scripts to bundled JS. (Not a bug.)
10. **Cilium / iptables / sysctl change about to be made** — STOP. Read root `CLAUDE.md` SSH safety rule. See `docs/postmortems/2026-03-31-cilium-ssh-incident.md`.

- [ ] **Step 4.5: Verify both runbooks exist with required sections**

```bash
test -f docs/runbooks/deploy.md && echo "deploy.md OK"
test -f docs/runbooks/troubleshooting.md && echo "troubleshooting.md OK"
grep -c "^## " docs/runbooks/deploy.md      # expect 8
grep -c "^### Symptom:" docs/runbooks/troubleshooting.md  # expect ≥ 10
```

- [ ] **Step 4.6: Commit**

```bash
git add docs/runbooks/
git commit -m "$(cat <<'EOF'
docs(runbooks): add deploy and troubleshooting runbooks

deploy.md: end-to-end runbook merging phase3-deploy-runbook + host-prep,
plus the new mandatory step of creating .lolday-secrets.env from the
example file.

troubleshooting.md: symptom-keyed lookup pointing at scripts/diag-*,
scripts/recover-*, and known incident patterns (Cilium SSH, schema head
mismatch, AUTH_DEV_MODE-in-production, CSP).

Per spec §8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Create `docs/postmortems/2026-03-31-cilium-ssh-incident.md`

**Files:**

- Create: `docs/postmortems/2026-03-31-cilium-ssh-incident.md`

**References:** spec §12.

- [ ] **Step 5.1: Write the postmortem placeholder (full content)**

```markdown
# 2026-03-31 — Cilium install broke SSH on server30

**Incident:** Attempted to install Cilium as a replacement CNI for K3s's
built-in flannel. After the Cilium agent started, host iptables rules were
flushed — including the rules that kept SSH on port 9453 reachable from
outside the lab network. The server was unreachable until physical access
was arranged for recovery.

**Why root `CLAUDE.md` has an SSH-safety hard rule:** the lab has no IPMI
or KVM-over-IP fallback for server30. Any change to CNI / iptables / UFW /
sysctl / firewall rules must be dry-runnable and the operator must verify
SSH from a fresh session before applying. This is now a hard rule on every
session.

**Status:** Cilium was not retained. server30's K3s currently uses the
built-in flannel CNI. Network-layer changes are gated by the SSH safety
rule.

**Follow-ups (none active):** if a future networking project requires
Cilium, plan it with operator-side fallback (out-of-band console / second
SSH path / staging environment).
```

- [ ] **Step 5.2: Verify file**

```bash
test -f docs/postmortems/2026-03-31-cilium-ssh-incident.md && wc -l docs/postmortems/2026-03-31-cilium-ssh-incident.md
```

Expected: file exists, ~20 lines.

- [ ] **Step 5.3: Commit**

```bash
git add docs/postmortems/2026-03-31-cilium-ssh-incident.md
git commit -m "$(cat <<'EOF'
docs(postmortems): add 2026-03-31 cilium SSH incident placeholder

Captures the incident that motivates the SSH-safety hard rule in
CLAUDE.md, so the rule has a discoverable rationale instead of dangling
from a deleted auto-memory reference.

Per spec §12.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Create env example files

**Files:**

- Create: `.lolday-secrets.env.example`
- Create: `.lolday-cf-svctoken.env.example`

**References:** spec §11.

- [ ] **Step 6.1: Confirm `.gitignore` will NOT exclude `*.env.example`**

```bash
grep -E "env\.example" .gitignore
```

Expected: `!*.env.example` (line 26 in current `.gitignore`).

- [ ] **Step 6.2: Write `.lolday-secrets.env.example` (full content)**

```bash
# Lolday operator-local secrets — copy to .lolday-secrets.env (chmod 600).
# This file is gitignored except for this .example. Fill values out-of-band.
#
# Loaded by: scripts/deploy.sh, scripts/recover-harbor.sh,
#            scripts/harbor-inventory.sh, scripts/fix-lolday-project-public.sh,
#            scripts/diag-backend-401.sh, scripts/phase6-pre-deploy-check.sh
# Also referenced by: charts/lolday/templates/monitoring/alertmanager-config-discord.yaml
#                     (operator wires the value into a K8s Secret out-of-band)

# Phase 6 — monitoring secrets
GRAFANA_ADMIN_PASSWORD=
PG_EXPORTER_PASSWORD=

# Phase 6 + Phase 10 — Cloudflare tunnel
CF_ENABLED=true
CF_TUNNEL_TOKEN=

# Phase 7.4 — Discord events webhook (also reused by Alertmanager)
DISCORD_WEBHOOK_URL_EVENTS=

# Phase 3 / Phase 9.6 recovery — Harbor
HARBOR_ADMIN_PASSWORD=

# Phase 3 — backend Fernet key for encrypted columns (32-byte base64)
FERNET_KEY=

# Add new keys here as they are introduced. Keep the comment block above
# each one explaining which phase / consumer needs it.
```

- [ ] **Step 6.3: Write `.lolday-cf-svctoken.env.example` (full content)**

```bash
# Cloudflare Access service token (machine principal) — copy to
# .lolday-cf-svctoken.env (chmod 600). Used to test svctoken auth via
# /users/me. See docs/phase-history/phase12.1-role-enum-bug.md for the
# debug context.

CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

- [ ] **Step 6.4: Verify both example files exist and are NOT gitignored**

```bash
ls -la .lolday-secrets.env.example .lolday-cf-svctoken.env.example
git check-ignore .lolday-secrets.env.example .lolday-cf-svctoken.env.example || echo "NOT ignored — correct"
```

Expected: both files exist; `git check-ignore` returns non-zero (i.e., they are tracked).

- [ ] **Step 6.5: Verify the real (non-example) files ARE gitignored**

```bash
git check-ignore -v .lolday-secrets.env .lolday-cf-svctoken.env
```

Expected: each line points at the `*.env` pattern in `.gitignore`.

- [ ] **Step 6.6: Commit**

```bash
git add .lolday-secrets.env.example .lolday-cf-svctoken.env.example
git commit -m "$(cat <<'EOF'
docs: add example env files for operator-local secrets

.lolday-secrets.env.example documents the keys consumed by deploy/
recovery/diag scripts. .lolday-cf-svctoken.env.example documents the
machine principal envs for svctoken testing. Real .env files remain
gitignored. Fixing operator onboarding gap where new sessions had no
discoverable list of required env keys.

Per spec §11.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Move scattered `docs/` files to `docs/phase-history/`

**Files:**

- `git mv` 11 files into `docs/phase-history/`
- Remove empty `docs/ops/`

**References:** spec §9.

- [ ] **Step 7.1: Create destination directory**

```bash
mkdir -p docs/phase-history
```

- [ ] **Step 7.2: Move scattered phase files**

```bash
git mv docs/2026-04-21-phase8-e2e-ux-findings.md  docs/phase-history/phase8-e2e-ux-findings.md
git mv docs/phase11b-e2e-checklist.md             docs/phase-history/
git mv docs/phase11d-retirement-findings.md       docs/phase-history/
git mv docs/phase11d-v0-snapshot.json             docs/phase-history/
git mv docs/phase12.1-role-enum-bug.md            docs/phase-history/
git mv docs/phase3-e2e-checklist.md               docs/phase-history/
git mv docs/phase4-e2e-checklist.md               docs/phase-history/
git mv docs/phase6-e2e-checklist.md               docs/phase-history/
git mv docs/phase7.5-e2e-checklist.md             docs/phase-history/
git mv docs/phase3-deploy-runbook.md              docs/phase-history/
git mv docs/ops/host-prep.md                      docs/phase-history/
```

- [ ] **Step 7.3: Remove empty `docs/ops/`**

```bash
rmdir docs/ops
```

- [ ] **Step 7.4: Verify `git mv` preserved history**

```bash
git log --follow --oneline docs/phase-history/phase3-deploy-runbook.md | head -3
```

Expected: at least one prior commit visible (the original creation commit).

- [ ] **Step 7.5: Verify `docs/` root is now clean**

```bash
ls docs/
```

Expected: only `architecture.md`, `conventions.md`, `phase-history/`, `postmortems/`, `runbooks/`, `superpowers/`. No more `phase*.md` at this level. No `ops/`.

- [ ] **Step 7.6: Verify `docs/phase-history/` has 11 entries**

```bash
ls docs/phase-history/ | wc -l
```

Expected: 11.

- [ ] **Step 7.7: Commit**

```bash
git add -A docs/
git commit -m "$(cat <<'EOF'
docs: move scattered phase files into docs/phase-history/

Consolidates phase E2E checklists, retirement findings, and the phase3
deploy runbook + host-prep doc under docs/phase-history/. The runbook
content is reborn in docs/runbooks/deploy.md (committed earlier in this
series); the originals stay here for blame/traceability.

Per spec §9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Rewrite `CLAUDE.md` (slim index)

**Files:**

- Modify: `CLAUDE.md` (full rewrite, < 100 lines).

**References:** spec §4.

- [ ] **Step 8.1: Snapshot current CLAUDE.md (already done in pre-flight)**

```bash
diff /tmp/lolday-pre-claude.md CLAUDE.md
```

Expected: no diff (file untouched so far).

- [ ] **Step 8.2: Replace `CLAUDE.md` with the new content (full content below, paste verbatim)**

````markdown
# Lolday — internal ML platform for ISLab malware detector management

@README.md

## How to navigate this codebase

- 系統架構 / 模組責任 / 外部服務 / env vars / 技術債 → `docs/architecture.md`
- 部署 / 維運 → `docs/runbooks/deploy.md`、`docs/runbooks/troubleshooting.md`
- 命名 / 分支 / commit / migration 慣例 → `docs/conventions.md`
- 在 `backend/` / `frontend/` / `charts/` / `scripts/` / `backend/migrations/` 工作 →
  自動載入對應 `.claude/rules/<area>.md`（path-scoped）
- 過去 Phase 紀錄 / E2E checklists → `docs/phase-history/`
- 事故 postmortem → `docs/postmortems/`
- Phase 設計 / 實作計畫 → `docs/superpowers/specs/`、`docs/superpowers/plans/`

## Hard rules（每個 session 都必須記得）

### SSH safety on server30

A broken SSH causes 重大的損失 — server30 has no IPMI / out-of-band fallback.
On 2026-03-31 a Cilium CNI install broke SSH and required physical recovery
(see `docs/postmortems/2026-03-31-cilium-ssh-incident.md`).

- Before any network / firewall / iptables / UFW / CNI / sysctl change, verify
  SSH will not be affected.
- Never modify UFW rules, iptables, or CNI config without dry-running and
  prompting the operator to verify SSH in a fresh session.
- After every infra step, verify SSH is still active.
- For dangerous operations, ask another agent to review first.

### Sudo policy

The operator normally has **no sudo** on server30. Sudo is granted temporarily
and then revoked.

- Install CLI tools at user level under `~/.local/bin/` (kubectl, helm, k9s,
  cilium, etc.). Never system-wide when a user-level install is possible.
- For sudo operations, **write the commands / scripts and hand them to the
  operator** — do not invoke sudo directly.
- In install / cleanup scripts, use `sudo` only on the specific lines that
  truly require it; comment them `# requires sudo`.

### Avoid China-origin software

ISLab is a Taiwanese security research lab. Default to English-ecosystem /
GitHub-mainstream software; flag China-origin choices for the operator to
approve.

- Component libraries: prefer **shadcn/ui, MUI, Chakra, Radix**. Avoid Ant
  Design, Arco (ByteDance), TDesign (Tencent), ElementUI, Naive UI.
- Forms / state / validation: prefer **TanStack, react-hook-form, zod, Redux
  Toolkit**.
- Cloud / SaaS: prefer **Cloudflare, GitHub, Vercel, Resend**.
- i18n: keep **zh-TW** as first-class, not zh-CN.
- Vite is an accepted gray zone (now Vercel-backed).
- Exception: use a China-origin tool when it has a clear advantage and no
  reasonable alternative — flag it explicitly first.

### Prefer open-source packages over custom code

Lolday is a glue platform. For every component, **first look for an existing
open-source / actively maintained project** before proposing a custom
implementation. Write custom code only for the glue layer and `maldet`-spec-
specific logic.

## Quickstart commands

```bash
bash scripts/install-tools.sh           # CLI tools, no sudo → ~/.local/bin/
sudo bash scripts/setup-k3s.sh          # K3s install — give to sudo-capable account
bash scripts/deploy.sh                  # platform deploy, no sudo
cd backend && uv run pytest             # backend tests
cd frontend && pnpm test                # frontend unit (vitest)
cd frontend && pnpm playwright test     # frontend E2E
helm lint charts/lolday                 # helm sanity
```
````

Detailed flow → `docs/runbooks/deploy.md` and `docs/architecture.md` §6.

## Project layout

| Path                                                                  | What                                 | Detailed rules                        |
| --------------------------------------------------------------------- | ------------------------------------ | ------------------------------------- | --------------------- |
| `backend/`                                                            | FastAPI + uv                         | `.claude/rules/backend.md`            |
| `frontend/`                                                           | Vite + React + TS                    | `.claude/rules/frontend.md`           |
| `charts/lolday/`                                                      | Helm umbrella + sub-charts + helpers | `.claude/rules/charts-and-helm.md`    |
| `scripts/`                                                            | install / deploy / diag / recover    | `.claude/rules/scripts-and-ops.md`    |
| `backend/migrations/`                                                 | Alembic                              | `.claude/rules/alembic-migrations.md` |
| `tests/phase7/`                                                       | shell-based smoke tests              | —                                     |
| `docs/superpowers/specs                                               | plans/`                              | Phase planning artefacts              | `docs/conventions.md` |
| `docs/{architecture,conventions,runbooks,phase-history,postmortems}/` | platform docs                        | this file                             |

````

- [ ] **Step 8.3: Verify line count < 100**

```bash
wc -l CLAUDE.md
````

Expected: < 100.

- [ ] **Step 8.4: Verify stale references gone**

```bash
grep -n "project_cilium_ssh_incident\|auto memory at\|For accumulated project facts" CLAUDE.md && echo "FAIL" || echo "OK"
```

Expected: `OK` (no matches).

- [ ] **Step 8.5: Verify all four hard rules still present**

```bash
grep -c "^### " CLAUDE.md
```

Expected: 4 (SSH safety, Sudo policy, Avoid China-origin software, Prefer open-source packages over custom code).

- [ ] **Step 8.6: Verify pointers resolve**

```bash
for p in docs/architecture.md docs/runbooks/deploy.md docs/runbooks/troubleshooting.md docs/conventions.md docs/postmortems/2026-03-31-cilium-ssh-incident.md docs/phase-history docs/postmortems docs/superpowers .claude/rules/backend.md .claude/rules/frontend.md .claude/rules/charts-and-helm.md .claude/rules/scripts-and-ops.md .claude/rules/alembic-migrations.md; do
  test -e "$p" && echo "OK: $p" || echo "MISS: $p"
done
```

Expected: every entry `OK`.

- [ ] **Step 8.7: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude): rewrite CLAUDE.md as slim index (< 100 lines)

Replaces the previous mixed-content CLAUDE.md with an index pointing at
docs/architecture.md, runbooks, conventions, postmortems, and the new
path-scoped .claude/rules/*.md. Hard rules (SSH safety, sudo, China-origin,
OSS-first) preserved verbatim. Removes stale references to auto-memory
files that no longer exist.

Per spec §4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Fix README.md broken phase1 link

**Files:**

- Modify: `README.md`

**References:** spec §3 (file tree shows `README.md ✏️`); spec §9 item 10 (broken link).

- [ ] **Step 9.1: Inspect current link**

```bash
grep -n "phase1-infrastructure" README.md
```

Expected: hit on a line like `[Phase 1 Plan (v2)](docs/superpowers/plans/2026-04-13-phase1-infrastructure-v2.md)`.

- [ ] **Step 9.2: Confirm real filename**

```bash
ls docs/superpowers/plans/ | grep phase1
```

Expected: `2026-03-30-phase1-infrastructure.md` (only one).

- [ ] **Step 9.3: Edit the link**

Replace `2026-04-13-phase1-infrastructure-v2.md` with `2026-03-30-phase1-infrastructure.md` in `README.md`. Update the visible label too: `[Phase 1 Plan]` (drop the `(v2)` since there's only one file).

- [ ] **Step 9.4: Verify**

```bash
grep -n "phase1-infrastructure" README.md
```

Expected: `[Phase 1 Plan](docs/superpowers/plans/2026-03-30-phase1-infrastructure.md)`.

```bash
test -f docs/superpowers/plans/2026-03-30-phase1-infrastructure.md && echo "OK"
```

Expected: `OK`.

- [ ] **Step 9.5: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): fix broken phase1 plan link

The link pointed at a v2 filename that never landed. Real file is
2026-03-30-phase1-infrastructure.md. Drop the "(v2)" qualifier.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Rebuild `MEMORY.md` (auto memory)

**Files:**

- Create / overwrite: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/MEMORY.md`

**References:** spec §10.

This file lives **outside** the repo. It is **not** committed.

- [ ] **Step 10.1: Confirm path exists**

```bash
ls -la ~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/
```

Expected: directory exists; `MEMORY.md` may be empty or absent.

- [ ] **Step 10.2: Write fresh `MEMORY.md` (full content)**

```markdown
# Auto memory — Lolday

This is Claude's auto-memory index for the lolday project. Topic files in
this directory are loaded on demand; only the first ~200 lines / 25KB of
this file are loaded at session start.

You-write facts about the project go in `CLAUDE.md`, `.claude/rules/`, or
`docs/`. Auto memory is reserved for things Claude learns: build commands
that worked, debugging insights, surprising behaviours, operator
preferences observed in past sessions.

## Index

(no entries yet — this directory was reset on 2026-04-29 as part of the
docs restructure)
```

- [ ] **Step 10.3: Verify**

```bash
wc -l ~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/MEMORY.md
ls ~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/
```

Expected: `MEMORY.md` is ~12 lines; no other topic files.

- [ ] **Step 10.4: NO commit**

This file is outside the repo. Do not attempt to `git add` it.

---

## Task 11: Final verification

**Files:** none modified.

**References:** spec §15 (acceptance criteria).

- [ ] **Step 11.1: Confirm no stale `project_cilium_ssh_incident` references in repo**

```bash
grep -rn "project_cilium_ssh_incident" . --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.mypy_cache --exclude-dir=.ruff_cache --exclude-dir=.pytest_cache --exclude-dir=dist --exclude-dir=test-results --exclude-dir=.worktrees --exclude-dir=.superpowers
```

Expected: no matches anywhere except possibly the spec / plan / postmortem (those are fine — they describe the rename).

If a hit exists in CLAUDE.md or any rule file, fix and re-commit before declaring done.

- [ ] **Step 11.2: Confirm no `auto memory at ~/.claude/projects` reference in `CLAUDE.md`**

```bash
grep -n "auto memory at" CLAUDE.md
```

Expected: no match.

- [ ] **Step 11.3: Confirm helm chart still lints**

```bash
cd /home/bolin8017/Documents/repositories/lolday
helm lint charts/lolday 2>&1 | tail -20
```

Expected: same exit status as before this PR (i.e., not a regression). Documentation changes should not affect helm lint, but run as a sanity check.

- [ ] **Step 11.4: Confirm `docs/` layout matches spec**

```bash
ls docs/
```

Expected (alphabetical): `architecture.md`, `conventions.md`, `phase-history`, `postmortems`, `runbooks`, `superpowers`. No `ops/`. No `phase*.md` at this level.

- [ ] **Step 11.5: Confirm all 5 rules files exist and have `paths:` frontmatter**

```bash
for f in .claude/rules/backend.md .claude/rules/frontend.md .claude/rules/charts-and-helm.md .claude/rules/scripts-and-ops.md .claude/rules/alembic-migrations.md; do
  test -f "$f" && head -2 "$f" | grep -q "^---$" && echo "OK: $f"
done
```

Expected: 5 `OK:` lines.

- [ ] **Step 11.6: Confirm `CLAUDE.md` < 100 lines and contains 4 hard-rule sections**

```bash
wc -l CLAUDE.md
grep -c "^### " CLAUDE.md
```

Expected: < 100, and `4`.

- [ ] **Step 11.7: Confirm 9 commits since the spec commit**

```bash
git log --oneline 9c0eb8f..HEAD | wc -l
git log --oneline 9c0eb8f..HEAD
```

Expected count: `9` (Tasks 1, 2, 3, 4, 5, 6, 7, 8, 9 each commit once; Tasks 10 and 11 do not commit).

Expected order (most recent first): `docs(readme)`, `docs(claude)`, `docs` (mv to phase-history), `docs` (env examples), `docs(postmortems)`, `docs(runbooks)`, `docs` (conventions), `docs` (architecture), `docs(rules)`.

- [ ] **Step 11.8: `git status` clean**

```bash
git status
```

Expected: working tree clean (apart from the unrelated `charts/lolday/helpers/build-helper/uv.lock` that was untracked at start; see pre-flight 0.1).

- [ ] **Step 11.9: Manually open and skim the new docs**

Open these in an editor and verify they read coherently:

- `CLAUDE.md`
- `docs/architecture.md`
- `docs/conventions.md`
- `docs/runbooks/deploy.md`
- `docs/runbooks/troubleshooting.md`

Subjective acceptance: a fresh session reading `CLAUDE.md` first, then drilling into `docs/architecture.md`, should be able to describe the system in 5 minutes without reading source code.

- [ ] **Step 11.10: Push to remote (operator decides)**

This plan does NOT push automatically. The operator chooses when to push and whether to open a PR. If the change is going through the new conventions, the PR title would be:

```
docs: restructure CLAUDE.md, add path-scoped rules, consolidate docs/
```

with a description block:

```
Spec: docs/superpowers/specs/2026-04-29-claude-md-restructure-design.md
Plan: docs/superpowers/plans/2026-04-29-claude-md-restructure.md
```

---

## Notes for the executing agent

- **Do not** touch `charts/lolday/helpers/build-helper/uv.lock` (untracked, unrelated).
- **Do not** modify any code under `backend/`, `frontend/`, `charts/`, `scripts/` beyond the file list above. The deferred work in spec §14 is explicitly out of scope.
- **Do not** rewrite history of past commits to align with the new conventions — the cut-over is forward-only.
- If `helm lint` was failing before this work started, that's pre-existing — don't fix in this plan.
- If you find a reference to a moved file (e.g., something pointing at `docs/phase3-e2e-checklist.md`) inside other files, fix the path inline in whichever task created/touched that file. Search:

  ```bash
  grep -rn "docs/phase3-e2e-checklist\|docs/phase4-e2e-checklist\|docs/phase6-e2e-checklist\|docs/phase7.5-e2e-checklist\|docs/phase11b-e2e-checklist\|docs/phase11d-retirement-findings\|docs/phase11d-v0-snapshot\|docs/phase12.1-role-enum-bug\|docs/phase8-e2e-ux-findings\|docs/phase3-deploy-runbook\|docs/ops/host-prep" . --exclude-dir=.git
  ```

  Any matches in tracked files should be updated to `docs/phase-history/<filename>` as part of the corresponding task or as a small follow-up commit. (Most matches will be in the spec / plan / `docs/superpowers/specs|plans/` historical files — leave those alone since they describe the past state.)
