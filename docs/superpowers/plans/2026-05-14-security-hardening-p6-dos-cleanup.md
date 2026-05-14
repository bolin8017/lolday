# Security Hardening P6 — DoS, Residual MEDIUM & LOW Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the long tail of hardening items and lift the platform's DoS tolerance to a state where a single malicious user cannot degrade service for others — by rate-limiting `/health`, streaming MLflow artifacts, capping outbound Discord notify concurrency, bounding the reconciler scan, planting a CSRF middleware, and sweeping 13 residual LOW findings across backend, frontend, charts, and docs.

**Architecture:** Eighteen findings collapsed into seventeen tasks across six chains. The **DoS chain (T1–T4)** rate-limits the public `/health` endpoint per IP, retargets kubelet's livenessProbe at the already-shipped `/livez` on the internal port 8001 (M-internal-split is P2 ground truth — `app/internal_app.py:18-21`), tunes the async SQLAlchemy pool, switches MLflow artifact download to `httpx.AsyncClient.stream` + FastAPI `StreamingResponse` behind a per-pod `asyncio.Semaphore(8)`, wraps Discord notify in a per-pod `asyncio.Semaphore(20)` with a `discord_notify_dropped` drop counter, and adds `.limit(200)` + `submitted_at ASC` ordering + a `RECONCILER_SCAN_TRUNCATED_TOTAL` counter to both reconciler scans. The **CSRF chain (T5)** plants a `CSRFOriginMiddleware` that, on `POST/PUT/PATCH/DELETE`, requires either `Sec-Fetch-Site: same-origin|none` or `Origin` matching `Host` — and fails-open on the both-absent path so non-browser clients (CLI, CF Access service tokens) pass through unchanged. The **backend hygiene chain (T6–T9)** swaps the per-experiment-stats `dict[str, asyncio.Lock]` for `weakref.WeakValueDictionary` (T6), adds `--filter=blob:limit=10m` to the build-pipeline `git clone` initContainer manifest (T7 — covers both L-clone-bandwidth and L-validator-size; see D7 for why), and validates `JOB_NAMESPACE` against `^[a-z0-9-]+$` at config-boot time so the f-string into PromQL cannot inject (T8). The **chart chain (T9–T13)** flips the frontend `imagePullPolicy` to `Always` (now safe behind the P4 digest pin), drops cloudflared to `runAsUser: 65532`, adds a `ResourceQuota` on the `monitoring` namespace, and deletes the dead `templates/registry.yaml` template + values block (registry.enabled has been `false` for the whole life of the chart — Harbor superseded it). The **frontend hardening chain (T14–T17)** adds a defense-in-depth WebSocket `event.origin` check, prefixes every `localStorage` key with `lolday.` (no migration — see D5), swaps three `window.location.href = ` SPA navigations for `useNavigate()`, and percent-encodes the `expId` / `runId` interpolation in the MLflow redirect (the lone `window.location.replace` site stays since `/mlflow/` isn't a TanStack route). The **docs chain (T18)** captures `L-samples-hostpath` as accepted tech debt in `docs/architecture.md` §10 — no code change.

**Tech Stack:** FastAPI 0.110+ middleware (Starlette `BaseHTTPMiddleware`), Python `asyncio.Semaphore`, `httpx.AsyncClient.stream` + Starlette `StreamingResponse`, SQLAlchemy 2.0 async (`pool_size` / `max_overflow`), `weakref.WeakValueDictionary`, Pydantic v2 `field_validator`, TanStack Router `useNavigate`, K8s `ResourceQuota`, K8s `imagePullPolicy: Always` + digest-pinned image (paired with P4 H-21-img), K8s `securityContext.runAsUser`, JavaScript `encodeURIComponent`, JavaScript `MessageEvent.origin` filter.

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.6 (P6 scope) + §11 (program-level acceptance gate).

**Finding IDs covered:** H-26, M-mlflow-stream, M-notify-semaphore, M-reconciler-limit, M-csrf, L-experiment-stats-lock, L-clone-bandwidth, L-validator-size (folded into T7), L-frontend-pull-policy, L-cloudflared-runas, L-monitoring-quota, L-ws-origin-check, L-localstorage-ns, L-window-location, L-location-replace-encode, L-registry-dead, L-samples-hostpath, L-promql-fstring (18 spec findings → 17 implementation tasks; T7 covers both L-clone-bandwidth + L-validator-size per D7).

---

## Design decisions (resolved up-front)

The implementer should not re-litigate these; they are locked.

**D1 — CSRF middleware scope, method set, and fail-open behaviour.** The middleware runs on every request to the public app (`backend/app/main.py:178+ FastAPI()` instance, not `internal_app`). It gates **state-changing methods only**: `POST`, `PUT`, `PATCH`, `DELETE`. `GET` / `HEAD` / `OPTIONS` pass through unconditionally per HTTP convention (idempotent / safe methods). Exclusion paths (skip gating, allow through):

| Path prefix                        | Why exempt                                                                                                                                                                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `/api/v1/internal/*`               | Job-token-authed; M-internal-split (P2) already isolates this on `internal_app` bound to `:8001` (not reachable on `:8000` per `main.py:253-254`). The path-prefix check is **defense-in-depth** — if the sub-app split is ever undone, CSRF still skips it. |
| `/api/v1/mlflow-authz`             | Traefik ForwardAuth target. Traefik calls this server-to-server (no browser cookie, no `Origin`, no `Sec-Fetch-Site`). It must not be CSRF-gated.                                                                                                            |
| `/metrics`                         | GET-only; falls through method check, but explicitly listed for clarity.                                                                                                                                                                                     |
| `/docs`, `/redoc`, `/openapi.json` | Same — read-only Swagger / ReDoc surfaces. `DOCS_ENABLED=false` in prod (H-26's P1 sibling M-docs-prod), but defensive.                                                                                                                                      |

Header logic on the remaining `/api/v1/*` state-changing surface:

```
if Sec-Fetch-Site header present:
    require Sec-Fetch-Site in {same-origin, none}  # none = address-bar nav; same-site is ambiguous, treated as cross-site
elif Origin header present:
    require scheme://host[:port] of Origin == scheme://host[:port] of Host
else:
    pass through  (fail-open on neither header — non-browser traffic: curl, CF Access service tokens, Python httpx without explicit Origin)
```

Browsers cannot suppress both `Origin` and `Sec-Fetch-Site` on a cross-site fetch — the both-absent path is structurally non-browser. Failing closed there would break every legitimate CLI integration (Cloudflare Access service tokens, operator curl, cron-style HTTP probes) without buying any browser-attack protection. OWASP CSRF cheat-sheet 2024 endorses the same pattern.

On rejection: return `403 Forbidden` with body `csrf check failed: <reason>`. No metric in this phase — rejection events are rare enough that Loki `kubectl logs deploy/backend | grep 'csrf check failed'` is the operator inspection path; if rejection rate becomes interesting, a P7 follow-up can add `CSRF_REJECTED_TOTAL{reason}`.

**D2 — `/livez` already exists; H-26 is a wiring change, not a new endpoint.** `app/internal_app.py:18-21` already declares `@internal_app.get("/livez")`. M-internal-split (P2) bound `internal_app` to `:8001` and added `containerPort: 8001` to backend.yaml. H-26's "/livez bound to :8001" line in the spec is therefore a no-op for the Python side. The implementation work is in the chart: `charts/lolday/templates/backend.yaml:112-117` retargets `livenessProbe` from `path: /api/v1/health, port: api` to `path: /livez, port: internal`. `readinessProbe` stays on `/api/v1/health, port: api` (readiness checks the public-traffic surface; liveness checks the unrate-limited internal surface — kubelet will not restart the pod because a `/health` 429 fired). DB-pool tuning (`db.py`) and the `/health` rate-limit dep (`main.py:290`) ship in the same task.

**D3 — Reconciler `.limit(200)` ordering: `submitted_at ASC, id ASC` for `Job`; `created_at ASC, id ASC` for `DetectorBuild`.** Spec says "order by `id` for resumability". `Job.id` and `DetectorBuild.id` are UUID v4 (random) — ordering by `id` gives a stable but uncorrelated sequence. The intent of resumability is "if the queue exceeds the cap, the oldest non-terminal rows are reconciled first" so that build/job state doesn't fall further behind under load. Timestamp-ASC ordering achieves this; the UUID tiebreaker keeps the order deterministic for tests. The new `lolday_reconciler_scan_truncated_total{kind}` counter increments when the scanned row count equals 200 (cap-hit indicator). No alert rule in this phase — a P7 follow-up can fire when the counter rate sustains, similar to L-discord-alert's pattern.

**D4 — `asyncio.Semaphore` sizing: 8 for MLflow stream, 20 for Discord notify (both per backend pod).** Validation:

- **MLflow stream `Semaphore(8)`** with 512 MiB pod memory limit. `httpx.AsyncClient.stream` reads in 64 KiB default chunks; FastAPI `StreamingResponse` immediately forwards them downstream. Peak transit buffer per stream ≈ 64–256 KiB. 8 concurrent 500 MiB downloads → ≤ 2 MiB resident in flight. Far below the 512 MiB pod cap (the rest is FastAPI / SQLAlchemy / cache state). Acceptance criterion #2 ("500 MiB download with 512 MiB pod limit → no OOMKill") is trivially satisfied. Mainstream reference: nginx default `client_body_buffer_size = 8k` × 1024 worker connections = 8 MiB at saturation; 2 MiB at our cap is more conservative.
- **Discord notify `Semaphore(20)`** with 5s `httpx` timeout × 2 replicas → ≤ 40 concurrent webhook posts platform-wide. Discord per-webhook rate limit (since 2022): **30 requests / 60 seconds**. Steady-state lolday traffic emits ≪ 1 notify/sec (job lifecycle is minutes-to-hours); the semaphore exists to bound a _runaway_ (e.g. reconciler loops over a 10k-row backlog, each row triggering a notify). 20 concurrent outbound HTTP requests is well inside `httpx.AsyncClient`'s default `max_keepalive_connections = 5` × `max_connections = 100`. Mainstream reference: AWS boto3 default `max_pool_connections = 10`; we're 2× that.

Both caps are conservative against their respective memory / outbound-RPS constraints and need no deviation from spec.

**D5 — `localStorage` prefix migration: break, no migration code.** Six existing localStorage write sites in `frontend/src/` use unprefixed keys (`runs.columns.${experimentId}`, `runs.status.${expId}`, ThemeProvider's `storageKey` parameter, `DISMISSED_KEY` in `_authed.models._index.tsx`, plus i18next's `i18nextLng` which is library-managed). After T15, all lolday-owned keys are prefixed `lolday.`. The pre-rename keys orphan in users' browser local storage and remain visible to `localStorage.length` / iteration — but no live code reads them, so they decay benignly until the user clears site data. The migration path (read old, write new, `lolday.migrated_v1` flag) is ~30 lines of one-shot bootstrap code; for a 5-user internal platform (ISLab researchers per `~/.claude/CLAUDE.md` §About me), the migration code is more risk than the state loss it prevents. Cost of break: each user re-picks theme (light/dark) and re-picks Runs table column visibility on first post-deploy visit. PR description must announce the break on Spidey Service Alerts (per `docs/operations.md` Discord channel directory) so users have ≤ 1 min of context before noticing. **i18next's `i18nextLng` key is NOT renamed** — that's a library-internal storage key, not lolday code; touching it forces library-config changes (`detection.lookupLocalStorage`) we don't need.

**D6 — `useNavigate()` versus `window.location` per call site.** Spec lists 4 frontend sites under L-window-location + L-location-replace-encode. Inspection-decided per site:

| File:line                           | Current call                                                       | Decision                                                                                                                                                                                                                                          |
| ----------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_authed.datasets._index.tsx:93`    | `window.location.href = \`/datasets/${d.id}\``                     | **useNavigate** — same-origin TanStack-managed route                                                                                                                                                                                              |
| `_authed.detectors._index.tsx:156`  | `window.location.href = \`/detectors/${d.id}\``                    | **useNavigate** — same-origin TanStack-managed route                                                                                                                                                                                              |
| `_authed.jobs._index.tsx:216`       | `window.location.href = \`/jobs/${j.id}\``                         | **useNavigate** — same-origin TanStack-managed route                                                                                                                                                                                              |
| `_authed.runs.$expId.$runId.tsx:19` | `window.location.replace(\`/mlflow/#/...${expId}/runs/${runId}\`)` | **Stay on `window.location.replace`** + add `encodeURIComponent` per L-location-replace-encode. `/mlflow/` is the reverse-proxied MLflow UI (not a TanStack route). Hash-routed inside the MLflow SPA; SPA-internal `useNavigate` won't reach it. |

L-window-location → 3 useNavigate replacements; L-location-replace-encode → keep the replace, percent-encode the segments. Both findings ship in T16 (useNavigate trio) + T17 (encode).

**D7 — `L-validator-size` is subsumed by `L-clone-bandwidth`.** The spec rationale assumes `backend/app/services/validator.py:28-37` contains a `git clone` invocation. **It does not.** `validator.py` operates on an already-cloned local repo (`repo_root: Path` argument) and runs four file-system checks (`_check_size`, `_check_pyproject`, `_check_dockerfile`, `_check_maldet_toml`). The actual `git clone` lives in the build-pipeline vcjob initContainer manifest at `backend/app/services/build.py:191-196` (`git -c credential.helper=... clone --depth=1 --recurse-submodules ...`). Adding `--filter=blob:limit=10m` to that single clone covers both finding IDs: validator runs against the already-bandwidth-capped clone tree, and `_check_size` (which `rglob`s the cloned tree against `settings.REPO_MAX_SIZE_MB`) already enforces a hard cap post-clone. T7 therefore covers both finding IDs; the plan body explicitly cross-references L-validator-size as "no-op, see D7". No code change to `validator.py`.

**D8 — `L-registry-dead` deletion is safe: `registry.enabled: false` is the only setting in tree.** `charts/lolday/values.yaml:14-19` declares `registry.enabled: false`; the whole `templates/registry.yaml` body is wrapped in `{{- if .Values.registry.enabled }}`. `helm template` and `helm get manifest` both confirm zero rendered resources today. Deleting the template + `registry:` values block is a tree-only change: no `helm upgrade` action against K8s state, no orphan resources to clean up. Verification step in T13 is `helm template charts/lolday | grep -c 'kind:.*registry'` → `0` before and after.

---

## Pre-flight

- [ ] **Confirm clean working tree on `main` at HEAD ≥ `b972696` (P5 ship + H-27 patch).**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git status
  git rev-parse HEAD
  ```

  Expected: working tree clean modulo untracked `backend/kube-prometheus-stack/` (unrelated upstream chart vendor dir). HEAD at `b972696` or newer.

- [ ] **Confirm helm rev ≥ 170 deployed with chart v0.22.1.**

  ```bash
  helm -n lolday list | grep lolday
  ```

  Expected: `REVISION 170` (or higher), `STATUS deployed`, `CHART lolday-0.22.1`, `APP VERSION 0.22.1`.

- [ ] **Confirm backend + frontend pods at v0.22.1, Running.**

  ```bash
  kubectl -n lolday get pods -l 'app in (backend,frontend)' \
    -o jsonpath='{range .items[*]}{.metadata.name}  {.status.phase}  {.spec.containers[0].image}{"\n"}{end}'
  ```

  Expected: both pods `Running`, image tag `v0.22.1@sha256:...`.

- [ ] **Confirm P5 counters and audit_log table did not regress.**

  ```bash
  kubectl -n lolday exec deploy/backend -- python -c "
  import urllib.request
  body = urllib.request.urlopen('http://localhost:8000/metrics', timeout=3).read().decode()
  for name in ('lolday_auth_failure_total','lolday_rate_limit_hits_total',
               'lolday_event_broker_drops_total','lolday_backend_errors_total'):
      defined = any(line.startswith('# TYPE ' + name) for line in body.splitlines())
      print(name, 'DEFINED' if defined else 'MISSING')
  "
  kubectl -n lolday exec postgresql-0 -- psql -U lolday -d lolday -c "\d audit_log" | head -5
  ```

  Expected: all four counters `DEFINED`; `audit_log` table reports columns `id, actor_id, action, target_type, target_id, before_jsonb, after_jsonb, ts`.

- [ ] **Confirm `internal_app.livez` is importable.**

  ```bash
  kubectl -n lolday exec deploy/backend -- /app/.venv/bin/python -c \
    "from app.internal_app import internal_app, livez; print('ok:', livez.__name__)"
  ```

  Expected: `ok: livez`. P2 wiring is intact and H-26's livenessProbe retarget can land safely.

- [ ] **Confirm backend test baseline = 773 passed.**

  ```bash
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: `773 passed` (732 base + 41 P5). P6 should land 773 + N new (N ≈ 18–25 across the backend-code tasks T1–T8).

- [ ] **Confirm `helm lint` baseline (post-P5 / P4 — Kyverno + audit-log alert rules wired).**

  Cache the canonical lint argv (P6 adds no new required values vs. P5):

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test \
    --set backend.fernetKeys=test \
    --set postgresql.auth.password=test \
    --set mlflow.auth.password=test --set mlflow.db.password=test \
    --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test \
    --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Confirm `pre-commit` baseline is green.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: all hooks green. **Do NOT use `--no-verify`** at any point in P6 (project hard rule).

- [ ] **Confirm `pnpm audit --prod` baseline.**

  ```bash
  cd frontend && pnpm install --frozen-lockfile && pnpm audit --prod
  ```

  Expected: 0 high / 0 critical. P6 acceptance criterion #5 re-runs the same check.

- [ ] **Create the feature branch.**

  ```bash
  cd /home/bolin8017/Documents/repositories/lolday
  git checkout -b security-hardening-p6
  ```

  The plan itself is committed directly to `main` (continuation of the security spec audit-trail pattern). All P6 task commits land on `security-hardening-p6` and squash-merge back to `main` via a single PR per the P1/P2/P3/P4/P5 pattern.

---

## Task 1: [H-26] /health rate-limit + DB pool tune + livenessProbe retarget at /livez

**Findings:** H-26 (HIGH). Recommended model: **opus** (touches three orthogonal surfaces — FastAPI dep injection, async DB engine config, chart probe wiring — with one acceptance test and one chart-render check).

**Files:**

- Modify: `backend/app/main.py:290-292` (add `Depends(rate_limit_ip("health", 120, 60))`)
- Modify: `backend/app/db.py:7` (extend `create_async_engine(...)` with `pool_size=20, max_overflow=30`)
- Modify: `charts/lolday/templates/backend.yaml:112-117` (retarget livenessProbe at `/livez` on port `internal`)
- Test: `backend/tests/test_health_rate_limit.py` (new file)

**Rationale:** Today `/api/v1/health` (returning `{"status":"ok"}`) has zero rate limiting. A pathological client (or attacker) hammering `/health` at 1000 RPS would exhaust the FastAPI request handler pool and starve every other endpoint of worker capacity; if `/health` is the kubelet livenessProbe target, a 503 from saturation triggers a restart, amplifying the outage. Two changes close this:

1. **Public `/health` gains an IP-keyed rate limit (120/60s).** 2 RPS sustained per IP. Real probes — Cloudflare Access health, browser status pings — never see this cap. A 1000-RPS attacker is converted to 2 RPS per source IP and 429 for the rest (the existing `lolday_rate_limit_hits_total{prefix="health"}` counter from P5 will surface this in Prometheus and `LoldayRateLimitSpike` will fire if sustained).
2. **kubelet's livenessProbe retargets `/livez` on `:8001`.** `internal_app.livez` (`app/internal_app.py:18-21`) is on the internal port that NetworkPolicy gates to `lolday-jobs` (per P2 M-internal-split). kubelet on the host reaches it via Pod IP + containerPort, not via the Service. The retarget makes liveness independent of `/health`'s rate-limit — even if `/health` is 100% 429-saturated, kubelet still sees the pod as live. readinessProbe stays on `/api/v1/health` because that surface is what external traffic actually hits; readiness reflecting saturation is desired (Cloudflared will route around a saturated replica).

DB pool tuning (`pool_size=20, max_overflow=30`) is a pre-emptive bump. Default SQLAlchemy is `pool_size=5, max_overflow=10` = 15 concurrent connections per pod × 2 replicas = 30 total. Postgres default `max_connections=100`; we're using 30%. Tuning to 50/pod × 2 = 100 brings us to exactly the Postgres cap. **This is tight against 2 replicas only** — adding a third replica would exceed Postgres's cap and demand `postgresql.max_connections` to bump. T1 documents this in a comment + flags it as tech debt for `docs/architecture.md` §10 update (folded into T18). The pool bump itself is uncontroversial; ⅔ headroom over current 15 → 50 gives FastAPI request handlers enough room to soak request bursts without queueing on the connection pool.

- [ ] **Step 1: Write the failing tests.**

  Create `backend/tests/test_health_rate_limit.py`:

  ```python
  """H-26: /health is IP-rate-limited at 120/60s; the 121st hit from a single IP returns 429."""

  from unittest.mock import AsyncMock, patch

  import pytest


  async def test_health_is_rate_limited_at_121st_hit_per_ip(client):
      """The dep returns 429 after 120 hits from the same IP in a window."""
      from app.services.rate_limit import check_rate

      # The 121st check returns False (over cap); first 120 are True.
      call_count = {"n": 0}

      async def fake_check_rate(key, limit, window_seconds):
          call_count["n"] += 1
          assert limit == 120
          assert window_seconds == 60
          assert key.startswith("rl:health:")
          return call_count["n"] <= 120

      with patch("app.services.rate_limit.check_rate", new=AsyncMock(side_effect=fake_check_rate)):
          # Burn 120 hits.
          for _ in range(120):
              r = await client.get("/api/v1/health")
              assert r.status_code == 200, r.text
          # 121st must be 429.
          r = await client.get("/api/v1/health")
          assert r.status_code == 429


  async def test_health_still_returns_ok_under_cap(client):
      """A single GET /health returns 200 + {'status':'ok'} (no rate-limit interference)."""
      r = await client.get("/api/v1/health")
      assert r.status_code == 200
      assert r.json() == {"status": "ok"}
  ```

  Fixture: `client` is the existing async test client in `backend/tests/conftest.py` (used throughout P5 tests).

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_health_rate_limit.py -v
  ```

  Expected: `test_health_is_rate_limited_at_121st_hit_per_ip` FAILS (no rate limit attached — call 121 still 200). `test_health_still_returns_ok_under_cap` PASSES already (existing behaviour).

- [ ] **Step 3: Attach the rate-limit dep to `/health` in `backend/app/main.py`.**

  Replace lines 290–292:

  ```python
  @app.get("/api/v1/health", tags=["system"])
  async def health():
      return {"status": "ok"}
  ```

  with:

  ```python
  # H-26: IP-keyed rate limit. 120/60s = 2 RPS per source — well above any
  # legitimate probe cadence (Cloudflare Access health check is 30s, browser
  # status ping is 60s, kubelet now targets /livez on :8001 instead). A 1000
  # RPS DoS attacker is converted to 2 RPS per IP + 429 for the rest, and
  # lolday_rate_limit_hits_total{prefix="health"} feeds LoldayRateLimitSpike
  # (P5). kubelet liveness is retargeted at /livez on :8001 in the chart so
  # this 429 does NOT cause pod restarts.
  from app.services.rate_limit import rate_limit_ip


  @app.get(
      "/api/v1/health",
      tags=["system"],
      dependencies=[Depends(rate_limit_ip("health", 120, 60))],
  )
  async def health():
      return {"status": "ok"}
  ```

  Add the `Depends` import at the top of the file if not already present:

  ```python
  from fastapi import Depends, FastAPI
  ```

  (It almost certainly is — `Depends` is used pervasively in this file.)

- [ ] **Step 4: Tune the async DB engine pool in `backend/app/db.py`.**

  Replace line 7:

  ```python
  engine = create_async_engine(settings.DATABASE_URL)
  ```

  with:

  ```python
  # H-26: pre-emptively size the connection pool so request bursts don't queue
  # on the pool checkout. 20 base + 30 overflow = 50 per pod × 2 replicas = 100
  # total — exactly Postgres default max_connections. Bumping replicas beyond 2
  # demands a parallel postgresql.max_connections bump (tracked as tech debt in
  # docs/architecture.md §10).
  engine = create_async_engine(
      settings.DATABASE_URL,
      pool_size=20,
      max_overflow=30,
  )
  ```

- [ ] **Step 5: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_health_rate_limit.py -v
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: both new tests PASS; full suite still green (773 + 2 = 775 passed).

- [ ] **Step 6: Retarget kubelet livenessProbe in `charts/lolday/templates/backend.yaml`.**

  Replace lines 112–117 (the `livenessProbe` block):

  ```yaml
  livenessProbe:
    httpGet:
      path: /api/v1/health
      port: api
    initialDelaySeconds: 15
    periodSeconds: 30
  ```

  with:

  ```yaml
  # H-26: kubelet liveness targets /livez on the internal port (:8001),
  # which has no rate limit and no DB / external dependency. /health
  # 429 (rate-limit overflow) MUST NOT trigger a pod restart.
  livenessProbe:
    httpGet:
      path: /livez
      port: internal
    initialDelaySeconds: 15
    periodSeconds: 30
  ```

  `readinessProbe` (lines 118–123) is unchanged — `/api/v1/health, port: api` remains, so a saturated replica drops out of the Service endpoint list under DoS until traffic abates.

- [ ] **Step 7: helm lint + render-check.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | yq 'select(.kind == "Deployment" and .metadata.name == "backend").spec.template.spec.containers[0].livenessProbe'
  ```

  Expected: lint clean; rendered livenessProbe shows `path: /livez, port: internal`.

- [ ] **Step 8: Commit.**

  ```bash
  git add backend/app/main.py backend/app/db.py \
    charts/lolday/templates/backend.yaml \
    backend/tests/test_health_rate_limit.py
  git commit -m "feat(dos): rate-limit /health + retarget livenessProbe at /livez [H-26]"
  ```

---

## Task 2: [M-mlflow-stream] Streaming MLflow artifact download + Semaphore(8)

**Findings:** M-mlflow-stream (MEDIUM). Recommended model: **opus** (rewrites a buffered `Response(content=r.content)` to a streaming pattern + adds per-pod semaphore + paired async test that asserts no full-buffer materialization).

**Files:**

- Modify: `backend/app/routers/experiments_proxy.py:299-343` (`download_artifact`)
- Test: `backend/tests/test_experiments_proxy_stream.py` (new file)

**Rationale:** Today, `download_artifact` does `r = await c.get(url); return Response(content=r.content, ...)`. `r.content` materializes the entire upstream response body in memory before the function returns. A 500 MiB artifact buffers 500 MiB resident; the pod limit is 512 MiB (per `backend.yaml`); under any meaningful concurrency the pod OOMKills. Spec acceptance criterion #2 forbids this. Two changes:

1. Switch to `httpx.AsyncClient.stream(...)` + FastAPI `StreamingResponse(...)`. Bytes flow through transit buffers ~64 KiB at a time; peak resident per stream stays in the hundreds-of-KiB range.
2. Wrap the call in `async with _MLFLOW_STREAM_SEM:` where `_MLFLOW_STREAM_SEM = asyncio.Semaphore(8)` is module-scoped (per-pod). Caps concurrent streams at 8 → ≤ 2 MiB resident in flight at maximum (8 × 256 KiB). D4 covers the size validation.

The Discord-notify pattern (already in place at `services/notify.py:46`) establishes the swallow-and-count discipline for outbound HTTP; here we use the **semaphore-wait** discipline because mid-stream cancellation by a client would otherwise hold the upstream socket open for the lifetime of the timeout — the semaphore + `async with httpx.stream(...)` context manager guarantee both are released.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_experiments_proxy_stream.py`:

  ```python
  """M-mlflow-stream: download_artifact streams via httpx.AsyncClient.stream + StreamingResponse."""

  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest


  async def test_download_artifact_streams_not_buffers(user_client, monkeypatch):
      """download_artifact must use httpx.stream + StreamingResponse, not r.content."""
      from app.routers import experiments_proxy

      # Mock the upstream MLflow get_run to return a run with the right artifact_uri.
      get_run_mock = AsyncMock(return_value={
          "info": {
              "run_id": "abc",
              "artifact_uri": "mlflow-artifacts:/exp/123/run/abc/artifacts",
          },
          "data": {"tags": [{"key": "lolday.user_id", "value": str(user_client.user.id)}]},
      })
      monkeypatch.setattr(experiments_proxy._client(), "get_run", get_run_mock)

      # Patch httpx.AsyncClient.stream so we can assert it's called (not .get).
      stream_used = {"called": False}

      class _FakeStream:
          status_code = 200
          headers = {"content-type": "application/octet-stream"}
          async def aiter_bytes(self, chunk_size=65536):
              for _ in range(10):
                  yield b"x" * 64 * 1024  # 640 KiB total, in 64 KiB chunks
          async def __aenter__(self):
              return self
          async def __aexit__(self, *a):
              return None

      class _FakeAsyncClient:
          def __init__(self, *a, **kw):
              pass
          async def __aenter__(self):
              return self
          async def __aexit__(self, *a):
              return None
          def stream(self, method, url):
              stream_used["called"] = True
              assert method == "GET"
              return _FakeStream()

      monkeypatch.setattr("app.routers.experiments_proxy.httpx.AsyncClient", _FakeAsyncClient)

      r = await user_client.get("/api/v1/runs/abc/artifacts/download?path=model.pkl")
      assert r.status_code == 200
      assert stream_used["called"] is True
      assert r.content.startswith(b"x" * 100)  # body is the streamed chunks concatenated
      # Content-Disposition still set (RFC 6266 helper from H-6).
      assert "model.pkl" in r.headers.get("content-disposition", "")


  async def test_download_artifact_semaphore_caps_concurrency():
      """When 9 simultaneous downloads attempt the same stream, the 9th waits."""
      import asyncio
      from app.routers import experiments_proxy

      sem = experiments_proxy._MLFLOW_STREAM_SEM
      assert sem._value == 8  # default value at module load

      # Acquire all 8 permits.
      acquired = []
      for _ in range(8):
          await sem.acquire()
          acquired.append(True)
      assert sem._value == 0

      # 9th must block; check it's not instantly schedulable.
      task = asyncio.create_task(sem.acquire())
      done, _pending = await asyncio.wait({task}, timeout=0.05)
      assert not done, "Semaphore(8) did not block on 9th acquire"

      # Release one, the 9th should proceed.
      sem.release()
      await asyncio.wait_for(task, timeout=0.5)
      # Restore module state.
      for _ in range(8):
          sem.release()
  ```

  Fixture `user_client` is the existing async client+user pair from `backend/tests/conftest.py` (same fixture used by P5 audit-log tests).

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_experiments_proxy_stream.py -v
  ```

  Expected: both FAIL — `_MLFLOW_STREAM_SEM` doesn't exist (`AttributeError`); the download still uses `c.get(url)` not `c.stream(...)`.

- [ ] **Step 3: Rewrite `download_artifact` in `backend/app/routers/experiments_proxy.py`.**

  Near the top of the file (after `_stats_locks: dict[...]` at line 78), add the module-level semaphore:

  ```python
  # M-mlflow-stream (security-hardening P6): cap concurrent MLflow artifact
  # streams per backend pod. 8 × ~256 KiB transit buffer = 2 MiB peak resident,
  # well under the 512 MiB pod limit even at saturation. See plan §D4 for
  # sizing validation.
  _MLFLOW_STREAM_SEM: asyncio.Semaphore = asyncio.Semaphore(8)
  ```

  Replace the body of `download_artifact` (lines 299–343):

  ```python
  @router.get("/runs/{run_id}/artifacts/download")
  async def download_artifact(
      run_id: str,
      path: str,
      user: Annotated[User, Depends(current_active_user)],
  ) -> StreamingResponse:
      try:
          run = await _client().get_run(run_id)
      except MlflowError as e:
          raise HTTPException(status_code=502, detail=str(e)) from e
      # H-1: ACL on owner.
      if not _user_can_see_run_dict(user, run):
          raise HTTPException(status_code=404, detail="run not found")
      # H-2: block traversal / absolute paths before interpolating ``path``.
      _validate_artifact_path(path)
      artifact_uri: str = run["info"]["artifact_uri"]
      prefix = "mlflow-artifacts:/"
      if not artifact_uri.startswith(prefix):
          raise HTTPException(
              status_code=502,
              detail=f"unexpected artifact_uri scheme: {artifact_uri!r}",
          )
      relative = artifact_uri[len(prefix) :].rstrip("/")
      # Percent-encode each segment defensively — ``..`` is already rejected
      # by ``_validate_artifact_path``, but unencoded ``#`` / ``?`` / ``%``
      # would otherwise truncate the upstream URL or get re-interpreted.
      safe_path = "/".join(quote(p, safe="") for p in PurePosixPath(path).parts)
      url = (
          f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/"
          f"{relative}/{safe_path}"
      )

      filename = PurePosixPath(path).name or "artifact"
      media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

      # M-mlflow-stream: stream rather than buffer. _MLFLOW_STREAM_SEM caps
      # in-flight streams to 8 per pod (see plan §D4). The semaphore + the
      # AsyncClient + the stream context manager all unwind on client cancel,
      # so we don't leak the upstream socket on premature disconnect.
      async def _iter_upstream():
          async with _MLFLOW_STREAM_SEM:
              async with httpx.AsyncClient(
                  timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS
              ) as client:
                  async with client.stream("GET", url) as upstream:
                      if upstream.status_code != 200:
                          # Drain to a string so the error message is meaningful;
                          # bounded because the upstream produced a non-2xx for
                          # an artifact-list URL (small JSON / HTML body).
                          body = await upstream.aread()
                          raise HTTPException(
                              status_code=502, detail=body.decode("utf-8", "replace")
                          )
                      async for chunk in upstream.aiter_bytes():
                          yield chunk

      return StreamingResponse(
          _iter_upstream(),
          media_type=media_type,
          headers={"Content-Disposition": build_content_disposition(filename)},
      )
  ```

  Update the top-of-file imports (line 10) — add `StreamingResponse`:

  ```python
  from fastapi import APIRouter, Depends, HTTPException, Query
  from fastapi.responses import StreamingResponse
  ```

  Remove the now-unused `Response` import if no other handler in the file uses it (grep `Response(` to confirm; if `list_artifacts` still returns one, leave it).

- [ ] **Step 4: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_experiments_proxy_stream.py -v
  cd backend && uv run pytest tests/ -q 2>&1 | tail -3
  ```

  Expected: both new tests PASS; full suite still green.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/routers/experiments_proxy.py \
    backend/tests/test_experiments_proxy_stream.py
  git commit -m "feat(dos): stream MLflow artifact download + Semaphore(8) [M-mlflow-stream]"
  ```

---

## Task 3: [M-notify-semaphore] Discord notify Semaphore(20) + drop counter

**Findings:** M-notify-semaphore (MEDIUM). Recommended model: **sonnet** (single-file backend edit, one stage label on the existing `BACKEND_ERRORS` Counter — no new Counter — paired test).

**Files:**

- Modify: `backend/app/services/notify.py:34-61` (`post_webhook`)
- Test: `backend/tests/test_notify_semaphore.py` (new file)

**Rationale:** `services/notify.py::post_webhook` today opens a fresh `httpx.AsyncClient` on every call. If a reconciler pass over a 10k-row backlog kicks off 10k `asyncio.create_task(notify_*(...))` calls in rapid succession, 10k concurrent outbound HTTP requests pile up — Discord's per-webhook rate limit (30/min) drops most of them, and the local pod thrashes asyncio scheduling. The mitigation: a per-pod `_NOTIFY_SEM = asyncio.Semaphore(20)`, and on failing acquire (`acquire(blocking=False)` returning False) increment `BACKEND_ERRORS{stage="discord_notify_dropped"}` — the existing P5 `LoldayDiscordNotifyFailing` alert keys on the `discord_notify` stage; the `_dropped` variant is a sibling label that wakes the same alert path if drops sustain. D4 covers the sizing.

Calling convention: producers continue to `asyncio.create_task(notify_*(...))` (fire-and-forget). The semaphore is acquired inside `post_webhook`; if it can't be acquired immediately, the notification is dropped (counter increments, log line at WARN). Spec wording is "failing acquire increments `BACKEND_ERRORS{stage="discord_notify_dropped"}`" — non-blocking acquire matches the fire-and-forget semantics of the caller.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_notify_semaphore.py`:

  ```python
  """M-notify-semaphore: post_webhook drops when _NOTIFY_SEM is saturated."""

  import asyncio

  import pytest
  from prometheus_client import REGISTRY


  def _read(metric: str, **labels) -> float:
      v = REGISTRY.get_sample_value(metric, labels=labels)
      return 0.0 if v is None else v


  async def test_notify_semaphore_drops_on_saturation(monkeypatch):
      """When all 20 permits are held, post_webhook drops + increments BACKEND_ERRORS{stage=discord_notify_dropped}."""
      from app.services import notify

      # Saturate the semaphore.
      sem = notify._NOTIFY_SEM
      assert sem._value == 20
      for _ in range(20):
          await sem.acquire()

      before = _read("lolday_backend_errors_total", stage="discord_notify_dropped")

      monkeypatch.setattr(notify.settings, "DISCORD_WEBHOOK_URL_EVENTS", "https://discord.test/x")
      await notify.post_webhook({"content": "test"})

      after = _read("lolday_backend_errors_total", stage="discord_notify_dropped")
      assert after - before == pytest.approx(1.0)

      # Restore.
      for _ in range(20):
          sem.release()


  async def test_notify_semaphore_passes_through_when_available(monkeypatch):
      """When permits are available, post_webhook proceeds (no drop counter increment)."""
      from unittest.mock import AsyncMock, MagicMock
      from app.services import notify

      before = _read("lolday_backend_errors_total", stage="discord_notify_dropped")

      monkeypatch.setattr(notify.settings, "DISCORD_WEBHOOK_URL_EVENTS", "https://discord.test/x")

      async def fake_post(*a, **kw):
          resp = MagicMock()
          resp.raise_for_status = MagicMock()
          return resp

      class _C:
          def __init__(self, *a, **kw): pass
          async def __aenter__(self): return self
          async def __aexit__(self, *a): return None
          post = AsyncMock(side_effect=fake_post)

      monkeypatch.setattr(notify.httpx, "AsyncClient", _C)

      await notify.post_webhook({"content": "test"})

      after = _read("lolday_backend_errors_total", stage="discord_notify_dropped")
      assert after == before
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_notify_semaphore.py -v
  ```

  Expected: both FAIL — `_NOTIFY_SEM` not defined (`AttributeError`).

- [ ] **Step 3: Add the semaphore + drop-on-saturation logic in `backend/app/services/notify.py`.**

  Append after the module-level `logger = ...` (around line 31):

  ```python
  # M-notify-semaphore (security-hardening P6): cap per-pod concurrent
  # webhook posts. 20 permits × 2 backend replicas → ≤ 40 outbound at any
  # moment; well below httpx default max_connections=100 and Discord's
  # per-webhook rate limit (30/60s). See plan §D4 for sizing validation.
  # Acquire is non-blocking — exceeded permits drop the notify (counted
  # in BACKEND_ERRORS{stage="discord_notify_dropped"}). The drop preserves
  # fire-and-forget semantics: producers `asyncio.create_task(notify_*())`
  # never block on this path.
  _NOTIFY_SEM: asyncio.Semaphore = asyncio.Semaphore(20)
  ```

  Add the `asyncio` import at the top:

  ```python
  import asyncio
  import logging
  from urllib.parse import urlparse
  ```

  Replace the body of `post_webhook` (lines 34–61):

  ```python
  async def post_webhook(payload: dict) -> None:
      url = settings.DISCORD_WEBHOOK_URL_EVENTS
      if not url:
          return
      host = urlparse(url).hostname or "?"

      # M-notify-semaphore: non-blocking acquire. Drop if saturated to
      # preserve fire-and-forget semantics. Use the private _value check
      # because asyncio.Semaphore lacks a public try_acquire() — the value
      # is stable here because we only ever read it from the same event loop.
      if _NOTIFY_SEM.locked() or _NOTIFY_SEM._value <= 0:
          BACKEND_ERRORS.labels(stage="discord_notify_dropped").inc()
          logger.warning(
              "Discord notify dropped (semaphore saturated): host=%s",
              host,
          )
          return

      async with _NOTIFY_SEM:
          try:
              async with httpx.AsyncClient(
                  timeout=settings.DISCORD_HTTP_TIMEOUT_SECONDS
              ) as client:
                  resp = await client.post(url, json=payload)
                  resp.raise_for_status()
          except httpx.HTTPStatusError as exc:
              BACKEND_ERRORS.labels(stage="discord_notify").inc()
              # M-discord-log: webhook URL is itself the secret — log host + status
              # only. Full path / token is the same value Discord uses to authenticate
              # the POST, so anything that lands in Loki is effectively the credential.
              logger.warning(
                  "Discord notify failed: status=%s host=%s",
                  exc.response.status_code,
                  host,
              )
          except Exception as exc:
              BACKEND_ERRORS.labels(stage="discord_notify").inc()
              logger.warning(
                  "Discord notify failed: error=%s host=%s",
                  type(exc).__name__,
                  host,
              )
  ```

  (Preserve the existing `M-discord-log` comment from P3 — only the surrounding semaphore is new.)

- [ ] **Step 4: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_notify_semaphore.py -v
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: both new tests PASS; full suite green.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/services/notify.py backend/tests/test_notify_semaphore.py
  git commit -m "feat(dos): cap discord notify concurrency at 20 [M-notify-semaphore]"
  ```

---

## Task 4: [M-reconciler-limit] Reconciler scan cap + ordering + truncated counter

**Findings:** M-reconciler-limit (MEDIUM). Recommended model: **sonnet** (touches reconciler/loop.py + metrics.py + paired test).

**Files:**

- Modify: `backend/app/reconciler/loop.py:62-78` (both `select(...)` queries)
- Modify: `backend/app/metrics.py` (append new Counter)
- Test: `backend/tests/test_reconciler_limit.py` (new file)

**Rationale:** Today `loop.py` does:

```python
res_builds = await session.execute(
    select(DetectorBuild).where(DetectorBuild.status.in_(IN_FLIGHT))
)
# ...
res_jobs = await session.execute(
    select(Job).where(Job.status.in_(NON_TERMINAL_STATUSES))
)
```

No `.limit(...)` cap. **Spec said line 55-78 has `.limit(200)`; that's stale — the code never carried a limit.** With no cap, a pathological queue (10k stuck Jobs) makes the reconciler loop linear in queue depth × wait time = blocks every subsequent reconcile and the loop falls behind. Three coupled changes:

1. **`.limit(200)`** on both queries — hard cap on rows scanned per iteration.
2. **`.order_by(...ASC)`** so the cap drops the _newest_ rows (oldest reconciled first; less urgent rows wait). Per D3: `Job.submitted_at ASC, Job.id ASC` and `DetectorBuild.created_at ASC, DetectorBuild.id ASC`.
3. **`RECONCILER_SCAN_TRUNCATED_TOTAL = Counter(..., labels=["kind"])`** — when the scan returned exactly 200 rows (cap-hit), increment for `kind="build"` or `kind="job"`.

D3 documents why timestamp ordering is the right answer (UUID ordering doesn't carry temporal meaning).

- [ ] **Step 1: Write the failing tests.**

  Create `backend/tests/test_reconciler_limit.py`:

  ```python
  """M-reconciler-limit: scan caps at 200 rows, orders oldest first, counter increments on cap-hit."""

  import uuid
  from datetime import datetime, timedelta, timezone

  import pytest
  from prometheus_client import REGISTRY


  def _read(metric: str, **labels) -> float:
      v = REGISTRY.get_sample_value(metric, labels=labels)
      return 0.0 if v is None else v


  async def test_reconciler_caps_job_scan_at_200_oldest_first(db_session, monkeypatch):
      """Seed 250 non-terminal Jobs; iteration scans 200 oldest by submitted_at."""
      from app.models import Job, JobStatus
      from app.reconciler.loop import _scan_jobs  # new helper introduced in Step 3
      from app.reconciler import jobs as reconcile_jobs_mod

      base = datetime.now(tz=timezone.utc) - timedelta(hours=10)
      seeded = []
      for i in range(250):
          j = Job(
              id=uuid.uuid4(),
              user_id=uuid.uuid4(),
              detector_id=uuid.uuid4(),
              detector_version="v1",
              dataset_config_id=uuid.uuid4(),
              status=JobStatus.queued_backend,  # any NON_TERMINAL_STATUSES member
              submitted_at=base + timedelta(seconds=i),
          )
          db_session.add(j)
          seeded.append(j)
      await db_session.commit()

      before = _read("lolday_reconciler_scan_truncated_total", kind="job")

      rows = await _scan_jobs(db_session, limit=200)
      assert len(rows) == 200
      # Oldest first — first row's submitted_at == base + 0s.
      assert rows[0].submitted_at == base
      assert rows[199].submitted_at == base + timedelta(seconds=199)

      # Cap-hit counter increments.
      after = _read("lolday_reconciler_scan_truncated_total", kind="job")
      assert after - before == pytest.approx(1.0)


  async def test_reconciler_caps_build_scan_at_200_oldest_first(db_session):
      """Same as above for DetectorBuild keyed by created_at."""
      from app.models.detector import DetectorBuild
      from app.reconciler.builds import IN_FLIGHT
      from app.reconciler.loop import _scan_builds

      # Seed via direct DB add; pick IN_FLIGHT[0] for status.
      status_val = next(iter(IN_FLIGHT))
      base = datetime.now(tz=timezone.utc) - timedelta(hours=5)
      for i in range(250):
          b = DetectorBuild(
              id=uuid.uuid4(),
              detector_id=uuid.uuid4(),
              tag="v1",
              status=status_val,
              created_at=base + timedelta(seconds=i),
          )
          db_session.add(b)
      await db_session.commit()

      before = _read("lolday_reconciler_scan_truncated_total", kind="build")

      rows = await _scan_builds(db_session, limit=200)
      assert len(rows) == 200
      assert rows[0].created_at == base
      after = _read("lolday_reconciler_scan_truncated_total", kind="build")
      assert after - before == pytest.approx(1.0)


  async def test_reconciler_counter_does_not_increment_below_cap(db_session):
      """Seed 50 rows; scan returns 50; counter does NOT increment."""
      from app.models import Job, JobStatus
      from app.reconciler.loop import _scan_jobs

      base = datetime.now(tz=timezone.utc) - timedelta(hours=20)
      for i in range(50):
          j = Job(
              id=uuid.uuid4(),
              user_id=uuid.uuid4(),
              detector_id=uuid.uuid4(),
              detector_version="v1",
              dataset_config_id=uuid.uuid4(),
              status=JobStatus.queued_backend,
              submitted_at=base + timedelta(seconds=i),
          )
          db_session.add(j)
      await db_session.commit()

      before = _read("lolday_reconciler_scan_truncated_total", kind="job")
      rows = await _scan_jobs(db_session, limit=200)
      assert len(rows) == 50
      after = _read("lolday_reconciler_scan_truncated_total", kind="job")
      assert after == before
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_limit.py -v
  ```

  Expected: all FAIL — `_scan_jobs` / `_scan_builds` not defined; counter not registered.

- [ ] **Step 3: Add the counter to `backend/app/metrics.py`.**

  Append after `EVENT_BROKER_DROPS_TOTAL` (added in P5 T3):

  ```python
  # M-reconciler-limit (security-hardening P6) — reconciler scan cap. Each
  # iteration of reconciler_loop scans at most RECONCILER_SCAN_LIMIT non-
  # terminal rows; this counter increments when the cap was hit (rows
  # returned == limit), partitioned by kind (build|job). A sustained
  # rate > 0 indicates the queue is growing faster than reconciliation
  # progresses; the cap protects iteration latency by capping per-iter
  # work. No alert rule in this phase — P7 follow-up if rate is interesting.
  RECONCILER_SCAN_TRUNCATED_TOTAL = Counter(
      "lolday_reconciler_scan_truncated_total",
      "Reconciler scan returned the cap limit — newer rows deferred to next iteration.",
      ["kind"],
  )
  ```

- [ ] **Step 4: Refactor `backend/app/reconciler/loop.py` to use bounded scan helpers.**

  Add module-level constant (after `HARBOR_ROTATE_EVERY_N_ITERATIONS` around line 51):

  ```python
  # M-reconciler-limit (security-hardening P6): hard cap on per-iteration scan.
  # See plan §D3 for ordering rationale.
  RECONCILER_SCAN_LIMIT = 200
  ```

  Add the two helpers (above `reconciler_loop`):

  ```python
  async def _scan_jobs(session, limit: int = RECONCILER_SCAN_LIMIT):
      """Return the oldest <= limit non-terminal jobs; increment the truncated
      counter when the scan hit the cap."""
      rows = (
          await session.execute(
              select(Job)
              .where(Job.status.in_(NON_TERMINAL_STATUSES))
              .order_by(Job.submitted_at.asc(), Job.id.asc())
              .limit(limit)
          )
      ).scalars().all()
      if len(rows) == limit:
          RECONCILER_SCAN_TRUNCATED_TOTAL.labels(kind="job").inc()
      return rows


  async def _scan_builds(session, limit: int = RECONCILER_SCAN_LIMIT):
      """Return the oldest <= limit in-flight detector builds; increment the
      truncated counter when the scan hit the cap."""
      rows = (
          await session.execute(
              select(DetectorBuild)
              .where(DetectorBuild.status.in_(IN_FLIGHT))
              .order_by(DetectorBuild.created_at.asc(), DetectorBuild.id.asc())
              .limit(limit)
          )
      ).scalars().all()
      if len(rows) == limit:
          RECONCILER_SCAN_TRUNCATED_TOTAL.labels(kind="build").inc()
      return rows
  ```

  Replace the inline `select(...)` calls inside `reconciler_loop` (lines 63-78):

  ```python
                  # Build reconcile pass
                  builds_to_reconcile = await _scan_builds(session)
                  for b in builds_to_reconcile:
                      try:
                          await reconcile_build(session, b)
                      except Exception:
                          BACKEND_ERRORS.labels(stage="reconcile_build").inc()
                          logger.exception(
                              "reconcile_build failed", extra={"build_id": str(b.id)}
                          )

                  # Job reconcile pass (Phase 4)
                  jobs_to_reconcile = await _scan_jobs(session)
                  for j in jobs_to_reconcile:
                      try:
                          await reconcile_job(session, j)
                      except Exception:
                          BACKEND_ERRORS.labels(stage="reconcile_job").inc()
                          logger.exception(
                              "reconcile_job failed", extra={"job_id": str(j.id)}
                          )
  ```

  Update the top-of-file imports (line 34) to add the counter:

  ```python
  from app.metrics import BACKEND_ERRORS, RECONCILER_SCAN_TRUNCATED_TOTAL
  ```

- [ ] **Step 5: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_reconciler_limit.py -v
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: three new tests PASS; existing reconciler tests still PASS.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/reconciler/loop.py backend/app/metrics.py \
    backend/tests/test_reconciler_limit.py
  git commit -m "feat(dos): cap reconciler scan at 200 oldest-first [M-reconciler-limit]"
  ```

---

## Task 5: [M-csrf] CSRF Origin / Sec-Fetch-Site middleware

**Findings:** M-csrf (MEDIUM). Recommended model: **opus** (new middleware, careful header logic, large paired test matrix covering 9 header combinations × method × excluded-path).

**Files:**

- Create: `backend/app/middleware/csrf.py` (new)
- Modify: `backend/app/main.py:186-188` (register the middleware alongside `BodySizeLimitMiddleware`)
- Test: `backend/tests/test_csrf_middleware.py` (new file)

**Rationale:** Cloudflare Access JWT is delivered both as cookie (`CF_Authorization`) and as header (`Cf-Access-Jwt-Assertion`). The cookie path is the CSRF-vulnerable surface: a cross-site `fetch('/api/v1/jobs', {method:'POST', credentials:'include'})` from `https://evil.example` carries the cookie automatically. Without a defense, attacker-controlled JavaScript on any non-lolday origin can trigger arbitrary state changes. The defense per D1: gate `POST/PUT/PATCH/DELETE` on `Sec-Fetch-Site: same-origin|none` OR `Origin` matching `Host`; fail-open on the both-absent path (CLI / service-token / non-browser traffic).

Browsers ALWAYS send `Origin` on cross-origin state-changing requests (since CORS spec, 2014); modern browsers (Chrome 76+, Firefox 90+, Safari 16.4+) also send `Sec-Fetch-Site`. The both-absent case is structurally not a browser request. The fail-open path is therefore safe against the threat we're defending (browser-side cross-site exploit) and does not break legitimate CLI / automation traffic (curl, httpx without explicit Origin, CF Access service tokens).

- [ ] **Step 1: Write the failing tests.**

  Create `backend/tests/test_csrf_middleware.py`:

  ```python
  """M-csrf: gate POST/PUT/PATCH/DELETE on Origin/Sec-Fetch-Site (see plan §D1)."""

  import pytest


  async def test_csrf_get_passes_without_headers(client):
      """Safe methods (GET/HEAD/OPTIONS) bypass CSRF check entirely."""
      r = await client.get("/api/v1/health")
      assert r.status_code == 200


  async def test_csrf_post_same_origin_sec_fetch_site_passes(user_client):
      """POST with Sec-Fetch-Site: same-origin passes."""
      # We use POST /datasets — any state-changing route works.
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "csrf-test", "csv_url": "s3://x/y.csv"},
          headers={"sec-fetch-site": "same-origin"},
      )
      # 422 / 400 from validation is fine — the CSRF middleware didn't reject.
      assert r.status_code != 403


  async def test_csrf_post_none_sec_fetch_site_passes(user_client):
      """POST with Sec-Fetch-Site: none (direct address-bar / bookmark) passes."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
          headers={"sec-fetch-site": "none"},
      )
      assert r.status_code != 403


  async def test_csrf_post_cross_site_sec_fetch_site_rejected(user_client):
      """POST with Sec-Fetch-Site: cross-site is rejected (403)."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
          headers={"sec-fetch-site": "cross-site"},
      )
      assert r.status_code == 403
      assert "csrf check failed" in r.text.lower()


  async def test_csrf_post_same_site_sec_fetch_site_rejected(user_client):
      """POST with Sec-Fetch-Site: same-site (NOT same-origin) is rejected (403).

      "same-site" means scheme + eTLD+1 match but origin (incl. port) differs;
      treat as cross-origin for CSRF purposes."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
          headers={"sec-fetch-site": "same-site"},
      )
      assert r.status_code == 403


  async def test_csrf_post_origin_matches_host_passes(user_client):
      """POST with Origin matching Host (no Sec-Fetch-Site) passes."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
          headers={"origin": "http://testserver", "host": "testserver"},
      )
      assert r.status_code != 403


  async def test_csrf_post_origin_mismatch_host_rejected(user_client):
      """POST with Origin different from Host is rejected (403)."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
          headers={"origin": "http://evil.example", "host": "testserver"},
      )
      assert r.status_code == 403


  async def test_csrf_post_neither_header_passes_fail_open(user_client):
      """POST with neither Origin nor Sec-Fetch-Site passes (non-browser path, fail-open per D1)."""
      r = await user_client.post(
          "/api/v1/datasets",
          json={"name": "x"},
      )
      assert r.status_code != 403


  async def test_csrf_internal_path_bypasses_check(client):
      """/api/v1/internal/* is exempt — defense-in-depth alongside the :8001 split."""
      # Even with explicit cross-site Sec-Fetch-Site, the path-prefix exempts it.
      r = await client.post(
          "/api/v1/internal/jobs/00000000-0000-0000-0000-000000000000/events",
          json={"kind": "test"},
          headers={"sec-fetch-site": "cross-site"},
      )
      # Whatever the 401/422/422 outcome, NOT 403-from-csrf.
      assert "csrf check failed" not in r.text.lower()


  async def test_csrf_mlflow_authz_path_bypasses_check(client):
      """/api/v1/mlflow-authz is the Traefik ForwardAuth target — exempt."""
      r = await client.post(
          "/api/v1/mlflow-authz",
          json={},
          headers={"sec-fetch-site": "cross-site"},
      )
      assert "csrf check failed" not in r.text.lower()
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_csrf_middleware.py -v
  ```

  Expected: every "rejected" test FAILS (no middleware to do the rejecting). "passes" tests already pass.

- [ ] **Step 3: Create `backend/app/middleware/csrf.py`.**

  ```python
  """M-csrf (security-hardening P6) — CSRF Origin / Sec-Fetch-Site middleware.

  Rejects ``POST/PUT/PATCH/DELETE`` requests on ``/api/v1/*`` unless the
  request signals a same-origin browser intent via either:
    1. ``Sec-Fetch-Site: same-origin`` or ``Sec-Fetch-Site: none``, or
    2. ``Origin`` whose scheme://host[:port] matches the ``Host`` header.

  Fails open when both headers are absent — that's structurally non-browser
  traffic (CLI, CF Access service tokens, Python httpx without explicit
  Origin). Browsers cannot suppress both on cross-site fetches, so a real
  CSRF attempt always carries at least an ``Origin``. See plan §D1.

  Excluded paths:
    - ``/api/v1/internal/*`` — job-token-authed; isolated on :8001 per P2,
      and this prefix is exempt as defense-in-depth.
    - ``/api/v1/mlflow-authz`` — Traefik ForwardAuth target, server-to-server,
      no browser headers ever attached.
  """

  from __future__ import annotations

  from urllib.parse import urlparse

  from starlette.middleware.base import BaseHTTPMiddleware
  from starlette.requests import Request
  from starlette.responses import Response

  _STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
  _EXEMPT_PREFIXES = (
      "/api/v1/internal/",
      "/api/v1/mlflow-authz",
  )
  _ALLOWED_SEC_FETCH_SITE = frozenset({"same-origin", "none"})


  def _origin_matches_host(origin: str, host: str) -> bool:
      """Return True iff Origin's scheme://host[:port] matches the request's Host.

      The Host header carries ``host[:port]`` (no scheme). For comparison, we
      strip the scheme + path from Origin and compare the netloc verbatim.
      """
      try:
          parsed = urlparse(origin)
      except Exception:
          return False
      if not parsed.netloc:
          return False
      return parsed.netloc == host


  class CSRFOriginMiddleware(BaseHTTPMiddleware):
      async def dispatch(self, request: Request, call_next):
          method = request.method.upper()
          if method not in _STATE_CHANGING_METHODS:
              return await call_next(request)

          path = request.url.path
          for prefix in _EXEMPT_PREFIXES:
              if path.startswith(prefix):
                  return await call_next(request)

          # Only gate /api/v1/* — anything outside (e.g. /metrics, /docs) is
          # GET-only or already an auth-free surface; no state to forge.
          if not path.startswith("/api/v1/"):
              return await call_next(request)

          sfs = request.headers.get("sec-fetch-site")
          origin = request.headers.get("origin")
          host = request.headers.get("host", "")

          if sfs is not None:
              if sfs not in _ALLOWED_SEC_FETCH_SITE:
                  return Response(
                      content=f"csrf check failed: Sec-Fetch-Site={sfs!r}",
                      status_code=403,
                      media_type="text/plain",
                  )
              return await call_next(request)

          if origin is not None:
              if not _origin_matches_host(origin, host):
                  return Response(
                      content=(
                          f"csrf check failed: Origin={origin!r} does not "
                          f"match Host={host!r}"
                      ),
                      status_code=403,
                      media_type="text/plain",
                  )
              return await call_next(request)

          # Both absent → non-browser path (CLI / service token). Fail open.
          return await call_next(request)
  ```

- [ ] **Step 4: Register the middleware in `backend/app/main.py`.**

  Just below the existing `BodySizeLimitMiddleware` registration (around line 188), add:

  ```python
  from app.middleware.body_size import BodySizeLimitMiddleware
  from app.middleware.csrf import CSRFOriginMiddleware

  app.add_middleware(BodySizeLimitMiddleware)
  # M-csrf: gate state-changing methods on Origin / Sec-Fetch-Site. See
  # backend/app/middleware/csrf.py and plan §D1.
  app.add_middleware(CSRFOriginMiddleware)
  ```

  Order matters: Starlette middleware is LIFO. `BodySizeLimitMiddleware` (added first) becomes the outer wrapper, `CSRFOriginMiddleware` the inner — so body-size rejection (413) fires before any CSRF check, which is correct (don't waste cycles inspecting headers on a body that will be rejected anyway).

- [ ] **Step 5: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_csrf_middleware.py -v
  cd backend && uv run pytest -q 2>&1 | tail -3
  ```

  Expected: all 10 new tests PASS; full suite still green.

- [ ] **Step 6: Commit.**

  ```bash
  git add backend/app/middleware/csrf.py backend/app/main.py \
    backend/tests/test_csrf_middleware.py
  git commit -m "feat(security): CSRF Origin / Sec-Fetch-Site middleware [M-csrf]"
  ```

---

## Task 6: [L-experiment-stats-lock] WeakValueDictionary for per-experiment Locks

**Findings:** L-experiment-stats-lock (LOW). Recommended model: **sonnet** (single-file edit + paired GC test).

**Files:**

- Modify: `backend/app/routers/experiments_proxy.py:78` (`_stats_locks`)
- Test: `backend/tests/test_experiments_proxy_stats_locks.py` (new file)

**Rationale:** Today `_stats_locks: dict[str, asyncio.Lock]` grows unbounded (one `Lock` per `experiment_id`, never evicted). At lab scale this is a tiny leak (`experiment_id` count stays ≤ 1k for the foreseeable future), but `WeakValueDictionary` is the mainstream Python idiom for "cache by key, but let GC reclaim entries when no caller still holds the value". Switching costs nothing and removes the leak entirely. The existing `# Acceptable: cache is capped at maxsize=64 and lab-scale...` comment becomes obsolete and is removed.

The change is one line + one import. The paired test seeds 100 keys, drops all references, runs `gc.collect()`, and asserts the dict shrinks.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_experiments_proxy_stats_locks.py`:

  ```python
  """L-experiment-stats-lock: _stats_locks is a WeakValueDictionary — entries GC'd when refs drop."""

  import asyncio
  import gc


  async def test_stats_locks_garbage_collected_after_local_refs_drop():
      """After acquiring + releasing locks for 50 experiments and dropping references,
      the underlying WeakValueDictionary shrinks below the initial count."""
      from app.routers import experiments_proxy

      assert hasattr(experiments_proxy._stats_locks, "_pending_removals") or \
             type(experiments_proxy._stats_locks).__name__ == "WeakValueDictionary"

      # Take a snapshot of the dict size before seeding.
      initial = len(experiments_proxy._stats_locks)

      # Seed 50 locks, hold them only locally.
      keys = [f"exp_{i}" for i in range(50)]
      locks = [experiments_proxy._stats_locks.setdefault(k, asyncio.Lock()) for k in keys]

      # While we hold strong refs, the dict should have at least 50 entries.
      assert len(experiments_proxy._stats_locks) >= initial + 50

      # Drop local strong refs and run GC.
      del locks
      gc.collect()

      # WeakValueDictionary should shrink back to near initial.
      assert len(experiments_proxy._stats_locks) <= initial + 5  # tolerate a handful from concurrent test churn
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_experiments_proxy_stats_locks.py -v
  ```

  Expected: FAILS — `_stats_locks` is a plain `dict`; the `WeakValueDictionary` instanceof check fails.

- [ ] **Step 3: Switch `_stats_locks` to `WeakValueDictionary` in `backend/app/routers/experiments_proxy.py`.**

  Add the `weakref` import at the top of the file (after `import asyncio` at line 1):

  ```python
  import asyncio
  import logging
  import mimetypes
  import weakref
  ```

  Replace line 78 (currently `_stats_locks: dict[str, asyncio.Lock] = {}` with its multi-line comment):

  ```python
  # L-experiment-stats-lock (security-hardening P6): WeakValueDictionary means
  # an entry is GC'd as soon as no caller still holds the Lock — no per-
  # experiment leak. Behaviourally equivalent to a plain dict for the
  # _experiment_stats hot path because callers retain a local reference for
  # the duration of the `async with lock:` block.
  _stats_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
  ```

- [ ] **Step 4: Run test to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_experiments_proxy_stats_locks.py -v
  ```

  Expected: PASSES.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/routers/experiments_proxy.py \
    backend/tests/test_experiments_proxy_stats_locks.py
  git commit -m "fix(memory): WeakValueDictionary for per-experiment stats locks [L-experiment-stats-lock]"
  ```

---

## Task 7: [L-clone-bandwidth + L-validator-size] Bandwidth-capped git clone in build pipeline

**Findings:** L-clone-bandwidth (LOW) + L-validator-size (LOW). Recommended model: **sonnet** (single-string edit + a regex-based regression test).

**Files:**

- Modify: `backend/app/services/build.py:191-196` (vcjob initContainer `args` for `clone`)
- Test: `backend/tests/test_build_clone_filter.py` (new file)

**Rationale:** The build-pipeline vcjob initContainer clones the detector repo with `git -c credential.helper=... clone --depth=1 --recurse-submodules --branch="$GIT_TAG" "https://github.com/$REPO.git" /workspace/src`. `--depth=1` already caps history depth, but per-file size is unbounded — a malicious detector repo can ship a 5 GiB binary, blow up the BuildKit ephemeral volume, and DoS the build queue. Adding `--filter=blob:limit=10m` (Git partial-clone, available since 2.20) makes the clone refuse blobs larger than 10 MiB during transfer. The 10 MiB cap matches the existing `BODY_SIZE_MAX_BYTES = 12 MiB` (H-24 / P1) and `settings.REPO_MAX_SIZE_MB` (the `validator._check_size` cap, settings-driven).

Per **D7**, this single edit covers both finding IDs: `validator.py` operates on the already-bandwidth-capped clone tree, and its `_check_size` post-clone walk gives a hard upper bound. No `validator.py` edit is needed (the spec's "validator.py:28-37 — apply same to validator" line ref is stale; the validator has no clone of its own).

The git protocol negotiation for `--filter=blob:limit=` requires the server to support partial clone. GitHub does. Self-hosted forges (Gitea, GitLab) ≥ 2018-era versions do. Lolday's detector authors all use GitHub per `docs/detector-repos.md` § detector inventory — this is non-controversial.

- [ ] **Step 1: Write the failing test.**

  Create `backend/tests/test_build_clone_filter.py`:

  ```python
  """L-clone-bandwidth: vcjob initContainer clone command includes --filter=blob:limit=10m."""

  import re


  def test_build_clone_command_has_blob_limit_filter():
      """The clone-init args string must include --filter=blob:limit=10m."""
      from app.services import build

      # The manifest is generated by build_vcjob_manifest (or similarly-named
      # public helper); the easiest assertion is on the source of the string
      # literal containing 'clone --depth=1'.
      import inspect
      src = inspect.getsource(build)
      assert "clone --depth=1" in src, "smoke check: clone literal still present"
      # Assert the partial-clone filter sits in the same clone literal.
      m = re.search(r'clone\s+--depth=1[^"\']*', src)
      assert m is not None
      assert "--filter=blob:limit=10m" in m.group(0), (
          "expected --filter=blob:limit=10m in the clone args; got: " + m.group(0)
      )
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_build_clone_filter.py -v
  ```

  Expected: FAILS — the regex matches but `--filter=` is not in the matched substring.

- [ ] **Step 3: Add `--filter=blob:limit=10m` to the clone command in `backend/app/services/build.py`.**

  Edit lines 191–196:

  ```python
                                  # H-19: git PAT must NOT appear in argv. Use git's
                                  # credential helper — the inline helper script
                                  # reads $GIT_USER and $GIT_TOKEN from env (which
                                  # are valueFrom: secretKeyRef, not visible in
                                  # kubectl describe pod) and echoes them on
                                  # stdout for git to consume. The clone URL no
                                  # longer carries any user:pass component.
                                  # L-clone-bandwidth: --filter=blob:limit=10m
                                  # refuses blobs > 10 MiB at transfer time —
                                  # caps disk + bandwidth from a malicious repo
                                  # before the validator (which itself enforces
                                  # REPO_MAX_SIZE_MB post-clone) runs. See plan
                                  # §D7 for why validator.py needs no separate
                                  # edit.
                                  "git -c credential.helper='!f() { echo username=$GIT_USER; echo password=$GIT_TOKEN; }; f' "
                                  "clone --depth=1 --filter=blob:limit=10m --recurse-submodules "
                                  '--branch="$GIT_TAG" '
                                  '"https://github.com/$REPO.git" '
                                  "/workspace/src && "
                                  "git -C /workspace/src rev-parse HEAD > /workspace/git-sha"
  ```

  (Only the second-to-last line of the clone literal changes; everything else is identical to the original.)

- [ ] **Step 4: Run test to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_build_clone_filter.py -v
  ```

  Expected: PASSES.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/services/build.py backend/tests/test_build_clone_filter.py
  git commit -m "fix(dos): bandwidth-cap detector clone via --filter=blob:limit=10m [L-clone-bandwidth L-validator-size]"
  ```

---

## Task 8: [L-promql-fstring] Validate JOB_NAMESPACE at config-boot

**Findings:** L-promql-fstring (LOW). Recommended model: **sonnet** (single-validator + paired tests).

**Files:**

- Modify: `backend/app/config.py` (add `field_validator` for `JOB_NAMESPACE`)
- Test: `backend/tests/test_config_validation.py` (extend existing file from P5 T11)

**Rationale:** `services/gpu_signal.py:222-225` interpolates `settings.JOB_NAMESPACE` into a PromQL query via f-string:

```python
k8s_namespace = settings.JOB_NAMESPACE
k8s_samples = _query_prometheus(
    f'DCGM_FI_DEV_GPU_UTIL{{exported_namespace="{k8s_namespace}"}}'
)
```

`JOB_NAMESPACE` is operator-set in chart values; today there's no shape check. If a future deploy accidentally sets `JOB_NAMESPACE='"} OR 1=1; DROP TABLE x; --'`, the f-string interpolates the literal into PromQL — Prometheus PromQL has no SQL injection equivalent (it's a query language with strict grammar), so the worst case is "the query parses incorrectly and Prometheus returns an error" rather than data theft. But the principle is still sound: validate the shape of any value that gets interpolated into a query string. `^[a-z0-9-]+$` is the Kubernetes-DNS-label regex (RFC 1123-style) — exactly the shape of any legitimate namespace name.

The validation runs at config-boot via Pydantic v2 `field_validator`, so a misconfiguration is a CrashLoopBackOff at startup, mirroring the P5 T11 pattern for `CF_ACCESS_TEAM_DOMAIN`.

- [ ] **Step 1: Write the failing tests.**

  Append to `backend/tests/test_config_validation.py`:

  ```python
  @pytest.mark.parametrize(
      "good",
      [
          "lolday-jobs",
          "lolday",
          "x",
          "a1-b2-c3",
      ],
  )
  def test_job_namespace_accepts_valid_dns_label(monkeypatch, good):
      monkeypatch.setenv("JOB_NAMESPACE", good)
      monkeypatch.setenv("ENVIRONMENT", "development")
      from app.config import Settings
      s = Settings()
      assert s.JOB_NAMESPACE == good


  @pytest.mark.parametrize(
      "bad",
      [
          "lolday-jobs;",
          "lolday-jobs OR 1=1",
          'lolday-jobs"} OR 1=1',
          "Lolday-Jobs",
          "lolday_jobs",
          "lolday.jobs",
          "",
          "lolday-jobs ",
      ],
  )
  def test_job_namespace_rejects_invalid(monkeypatch, bad):
      monkeypatch.setenv("JOB_NAMESPACE", bad)
      monkeypatch.setenv("ENVIRONMENT", "development")
      from app.config import Settings
      with pytest.raises(ValidationError) as ei:
          Settings()
      assert "JOB_NAMESPACE" in str(ei.value)
  ```

- [ ] **Step 2: Run tests to verify failure.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k job_namespace
  ```

  Expected: all `_rejects_invalid` cases FAIL (no validator); `_accepts_valid` cases PASS already (no validator means no rejection).

- [ ] **Step 3: Add the `field_validator` to `backend/app/config.py`.**

  Insert immediately after the `_validate_cf_access_team_domain` validator added in P5 T11 (around line 130):

  ```python
      @field_validator("JOB_NAMESPACE")
      @classmethod
      def _validate_job_namespace(cls, v: str) -> str:
          """L-promql-fstring (security-hardening P6).

          ``JOB_NAMESPACE`` is interpolated into a PromQL f-string in
          ``services/gpu_signal.py`` (the host-aware GPU signal query). PromQL
          itself has no injection-equivalent of SQL, but any operator-set value
          that lands in a query string ought to match a defensive shape. We
          require the standard Kubernetes DNS-label form (RFC 1123) — the only
          shape a legitimate namespace can have anyway.
          """
          import re
          if not re.fullmatch(r"[a-z0-9-]+", v):
              raise ValueError(
                  f"JOB_NAMESPACE={v!r} is not a valid Kubernetes DNS label "
                  "(expected lowercase letters, digits, hyphens; non-empty)."
              )
          return v
  ```

- [ ] **Step 4: Run tests to verify pass.**

  ```bash
  cd backend && uv run pytest tests/test_config_validation.py -v -k job_namespace
  ```

  Expected: all PASS.

- [ ] **Step 5: Commit.**

  ```bash
  git add backend/app/config.py backend/tests/test_config_validation.py
  git commit -m "feat(config): validate JOB_NAMESPACE shape at boot [L-promql-fstring]"
  ```

---

## Task 9: [L-frontend-pull-policy] Frontend imagePullPolicy: Always

**Findings:** L-frontend-pull-policy (LOW). Recommended model: **sonnet** (single-line chart edit + helm render check).

**Files:**

- Modify: `charts/lolday/templates/frontend.yaml:16` (`imagePullPolicy`)

**Rationale:** Frontend image is digest-pinned post-P4 (`@sha256:<digest>` in `values.yaml`). With digest pinning, `imagePullPolicy: Always` is **safe**: kubelet pulls only when the digest isn't already in the node's image cache (digest is content-addressed). On node restart, the pull confirms the digest matches; if Harbor was compromised and a tag re-pointed to a malicious image, the digest mismatch would prevent the pull and the pod would fail (which is the desired behaviour). Without the digest pin, `Always` would be a risk (always pull means always pull whatever tag points at), but the P4 pin neutralises that.

Backend already runs at `imagePullPolicy: IfNotPresent` per the existing chart pattern; this finding targets frontend specifically because the post-P4 audit found frontend.yaml unchanged from pre-P4 state.

- [ ] **Step 1: Edit `charts/lolday/templates/frontend.yaml`.**

  Replace line 16:

  ```yaml
  imagePullPolicy: IfNotPresent
  ```

  with:

  ```yaml
  # L-frontend-pull-policy: with the P4 digest pin (H-21-img), Always
  # is safe — the digest is content-addressed and kubelet skips the
  # actual pull when the layer is already cached. If Harbor were
  # ever compromised to re-point a tag, the digest check on pull
  # would refuse the swap.
  imagePullPolicy: Always
  ```

- [ ] **Step 2: helm lint + render check.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | yq 'select(.kind == "Deployment" and .metadata.name == "frontend").spec.template.spec.containers[0].imagePullPolicy'
  ```

  Expected: lint clean; rendered policy is `"Always"`.

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/templates/frontend.yaml
  git commit -m "chart(frontend): imagePullPolicy Always (safe behind P4 digest pin) [L-frontend-pull-policy]"
  ```

---

## Task 10: [L-cloudflared-runas] Cloudflared runAsUser: 65532

**Findings:** L-cloudflared-runas (LOW). Recommended model: **sonnet** (chart-only).

**Files:**

- Modify: `charts/lolday/templates/cloudflared.yaml` (pod-level `securityContext`)

**Rationale:** Cloudflared's pod-level `securityContext` already sets `runAsNonRoot: true` (line 21). Without `runAsUser`, the container runs as the image's `USER` directive — for `cloudflare/cloudflared:2026.3.0` that's UID `65532` already (the upstream image sets a non-root user), so the change is **explicit pinning of an already-correct value**. Explicit `runAsUser: 65532` makes the chart self-documenting and resilient against an upstream image change that might silently switch USER to something else. 65532 is the conventional `nobody`-style UID used by distroless images and recommended by NSA-CISA Kubernetes Hardening Guide.

- [ ] **Step 1: Edit `charts/lolday/templates/cloudflared.yaml`.**

  Locate the pod-level `securityContext` block (around line 20-22):

  ```yaml
  securityContext:
    runAsNonRoot: true
    seccompProfile: { type: RuntimeDefault }
  ```

  Replace with:

  ```yaml
  securityContext:
    runAsNonRoot: true
    # L-cloudflared-runas: pin the UID explicitly. The upstream
    # cloudflare/cloudflared image already runs as 65532 (distroless
    # nobody convention); the explicit pin defends against an upstream
    # USER change.
    runAsUser: 65532
    seccompProfile: { type: RuntimeDefault }
  ```

- [ ] **Step 2: helm lint + render check.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | yq 'select(.kind == "Deployment" and .metadata.name == "cloudflared").spec.template.spec.securityContext.runAsUser'
  ```

  Expected: lint clean; rendered runAsUser is `65532`.

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/templates/cloudflared.yaml
  git commit -m "chart(cloudflared): pin runAsUser to 65532 [L-cloudflared-runas]"
  ```

---

## Task 11: [L-monitoring-quota] ResourceQuota on monitoring namespace

**Findings:** L-monitoring-quota (LOW). Recommended model: **sonnet** (new chart template).

**Files:**

- Create: `charts/lolday/templates/monitoring/quota.yaml`

**Rationale:** The `monitoring` namespace hosts Prometheus + Grafana + Alertmanager + Loki + Alloy + (eventually) more. Today there's no `ResourceQuota`. A buggy chart upgrade that accidentally requests `replicas: 100` for Grafana, or a leaked credential that lets someone spam `kubectl apply` from `monitoring`, has no namespace-level brake. The fix: a `ResourceQuota` capping `pods`, `count/replicasets`, and `persistentvolumeclaims` at sensible upper bounds. Mainstream reference: kube-prometheus-stack's own helm values document a `prometheusOperator.resources` cap but don't ship a namespace-wide quota — adding one is a standard NSA-CISA recommendation.

The actual numbers are derived from current state + 2x headroom: today `kubectl -n monitoring get pods | wc -l` ≈ 6 pods, ≈ 8 replicasets, 2 PVCs (Prometheus + Loki). Cap at 20 pods, 30 replicasets, 5 PVCs. Anything legitimate stays well under; runaway creation hits the cap.

- [ ] **Step 1: Create `charts/lolday/templates/monitoring/quota.yaml`.**

  ```yaml
  # L-monitoring-quota (security-hardening P6): cap workload counts in the
  # monitoring namespace. Numbers derived from current state (≤ 6 pods,
  # ≤ 8 replicasets, 2 PVCs) + 2-3x headroom. Anything legitimate stays
  # well under; runaway upgrade-loop spam hits the cap.
  apiVersion: v1
  kind: ResourceQuota
  metadata:
    name: monitoring-quota
    namespace: { { .Values.monitoring.namespace | default "monitoring" } }
    labels: { { - include "lolday.labels" . | nindent 4 } }
  spec:
    hard:
      pods: "20"
      count/replicasets.apps: "30"
      persistentvolumeclaims: "5"
  ```

- [ ] **Step 2: helm lint + render check.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | yq 'select(.kind == "ResourceQuota" and .metadata.name == "monitoring-quota")'
  ```

  Expected: lint clean; rendered ResourceQuota present with the three hard caps.

- [ ] **Step 3: Commit.**

  ```bash
  git add charts/lolday/templates/monitoring/quota.yaml
  git commit -m "chart(monitoring): ResourceQuota capping pods/rs/PVCs [L-monitoring-quota]"
  ```

---

## Task 12: [L-registry-dead] Delete the dead registry template + values block

**Findings:** L-registry-dead (LOW). Recommended model: **sonnet** (file delete + values trim).

**Files:**

- Delete: `charts/lolday/templates/registry.yaml`
- Modify: `charts/lolday/values.yaml:14-19` (remove the `registry:` block)

**Rationale:** Per **D8**, `registry.enabled` has been `false` for the whole life of the chart and Harbor (`charts/lolday/charts/harbor-1.18.3.tgz`) superseded the in-tree `registry:2` template. The whole `templates/registry.yaml` body is wrapped in `{{- if .Values.registry.enabled }}` — `helm template` returns zero rendered resources today. Deleting both files is a tree-only change; no `helm upgrade` action against K8s state.

Verification: `helm template ... | grep -c registry` returns `0` before and after the edit. `kubectl -n lolday get all -l app.kubernetes.io/component=registry` returns empty.

- [ ] **Step 1: Verify the registry has zero rendered resources today.**

  ```bash
  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | grep -c 'name: registry-data\|name: registry'
  ```

  Expected: `0`.

  Then verify no live resources either:

  ```bash
  kubectl -n lolday get deploy,svc,pvc -l app.kubernetes.io/component=registry --no-headers 2>&1 | head -5
  ```

  Expected: `No resources found in lolday namespace.`

- [ ] **Step 2: Delete the template and trim values.**

  ```bash
  rm charts/lolday/templates/registry.yaml
  ```

  Edit `charts/lolday/values.yaml` to remove the `registry:` block (lines 14-19):

  ```yaml
  # Before — lines 14-19:
  # =============================================================================
  # Private Container Registry (registry:2)
  # =============================================================================
  registry:
    enabled: false
    storage:
      size: 50Gi
      # Uses K3s default StorageClass (local-path)

  # After — entire block deleted; the next section (Cloudflare Tunnel) shifts up.
  ```

- [ ] **Step 3: helm lint + render check.**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test

  helm template charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test \
    2>/dev/null | grep -c 'name: registry'
  ```

  Expected: lint clean; second grep returns `0`.

- [ ] **Step 4: Commit.**

  ```bash
  git add charts/lolday/templates/registry.yaml charts/lolday/values.yaml
  git commit -m "chart(registry): delete dead template + values block (superseded by Harbor) [L-registry-dead]"
  ```

  Note: `git add` of a deleted file stages the deletion. Equivalent: `git rm charts/lolday/templates/registry.yaml`.

---

## Task 13: [L-ws-origin-check] WebSocket event.origin check in useJobEvents

**Findings:** L-ws-origin-check (LOW). Recommended model: **sonnet** (single-file frontend edit + Vitest test).

**Files:**

- Modify: `frontend/src/hooks/useJobEvents.ts:91-104` (event handler)
- Test: `frontend/src/hooks/useJobEvents.test.ts` (new file)

**Rationale:** `useJobEvents` opens a WebSocket and processes incoming `MessageEvent`. WebSockets bypass the browser's same-origin policy for connect (the server is supposed to validate `Origin`), but the client-side handler can also short-circuit messages whose `ev.origin` doesn't match `window.origin` — defense-in-depth against a malicious extension or another site script attempting to inject. Today there's no origin check at the handler.

WebSocket `MessageEvent.origin` is the scheme://host[:port] of the WebSocket server URL — for lolday's same-origin `wss://lolday.connlabai.com/...` it's `https://lolday.connlabai.com`, matching `window.origin`. A cross-origin WS would produce a mismatch.

- [ ] **Step 1: Write the failing test.**

  Create `frontend/src/hooks/useJobEvents.test.ts`:

  ```typescript
  import { renderHook, act } from "@testing-library/react";
  import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
  import { useJobEvents } from "./useJobEvents";

  describe("useJobEvents — L-ws-origin-check", () => {
    let originalOrigin: string;
    let ws: any;

    beforeEach(() => {
      originalOrigin = window.location.origin;
      // Capture the WebSocket constructed by the hook so the test can fire
      // synthetic MessageEvents at it.
      ws = null;
      vi.stubGlobal(
        "WebSocket",
        class FakeWS {
          onmessage: ((ev: any) => void) | null = null;
          onopen: ((ev: any) => void) | null = null;
          onclose: ((ev: any) => void) | null = null;
          onerror: ((ev: any) => void) | null = null;
          send = vi.fn();
          close = vi.fn();
          readyState = 1;
          constructor(public url: string) {
            ws = this;
          }
        },
      );
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("drops messages whose origin does not match window.origin", () => {
      const onEvent = vi.fn();
      renderHook(() => useJobEvents("job-id-123", onEvent));
      expect(ws).not.toBeNull();

      act(() => {
        ws.onmessage?.({
          origin: "https://evil.example",
          data: JSON.stringify({ kind: "test" }),
        });
      });

      expect(onEvent).not.toHaveBeenCalled();
    });

    it("processes messages whose origin matches window.origin", () => {
      const onEvent = vi.fn();
      renderHook(() => useJobEvents("job-id-123", onEvent));
      expect(ws).not.toBeNull();

      act(() => {
        ws.onmessage?.({
          origin: originalOrigin,
          data: JSON.stringify({ kind: "test", payload: { foo: 1 } }),
        });
      });

      expect(onEvent).toHaveBeenCalledTimes(1);
      expect(onEvent).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "test" }),
      );
    });
  });
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd frontend && pnpm test src/hooks/useJobEvents.test.ts --run
  ```

  Expected: "drops messages whose origin does not match" FAILS — origin is currently not checked.

- [ ] **Step 3: Add the origin check in `frontend/src/hooks/useJobEvents.ts`.**

  Locate the `ws.onmessage = (ev) => { ... }` handler (around lines 91-104). At the top of the callback body, add:

  ```typescript
  ws.onmessage = (ev) => {
    // L-ws-origin-check (security-hardening P6): defense-in-depth against
    // a malicious extension or off-origin script injecting messages. The
    // backend already validates Origin on WS handshake, but the client
    // refusing off-origin frames costs nothing.
    if (ev.origin && ev.origin !== window.location.origin) {
      return;
    }
    // ... rest of the existing handler body (JSON.parse, dispatch, etc.) ...
  };
  ```

- [ ] **Step 4: Run test to verify pass.**

  ```bash
  cd frontend && pnpm test src/hooks/useJobEvents.test.ts --run
  ```

  Expected: both tests PASS.

- [ ] **Step 5: Commit.**

  ```bash
  git add frontend/src/hooks/useJobEvents.ts frontend/src/hooks/useJobEvents.test.ts
  git commit -m "fix(frontend): drop WS messages with mismatched origin [L-ws-origin-check]"
  ```

---

## Task 14: [L-localstorage-ns] Prefix every lolday-owned localStorage key with `lolday.`

**Findings:** L-localstorage-ns (LOW). Recommended model: **sonnet** (3-file edit + manual smoke).

**Files:**

- Modify: `frontend/src/components/ThemeProvider.tsx:21,31,64-66` (default storageKey)
- Modify: `frontend/src/components/runs/RunsColumnPicker.tsx:29,86` (column-picker storage key)
- Modify: `frontend/src/routes/_authed.runs.$expId.tsx:70,74` (status filter key)
- Modify: `frontend/src/routes/_authed.models._index.tsx:131-153` (`DISMISSED_KEY`)

**Rationale:** Per D5, all lolday-owned localStorage keys gain a `lolday.` prefix. No migration code; users re-pick theme / column visibility / dismissed banner state. i18next's `i18nextLng` is library-managed and NOT touched.

Concrete key changes:

| Site                                        | Before                               | After                                 |
| ------------------------------------------- | ------------------------------------ | ------------------------------------- |
| `ThemeProvider.tsx` default `storageKey`    | `"lolday-theme"` (already prefixed?) | confirm; if not, `"lolday.theme"`     |
| `RunsColumnPicker.tsx`                      | `runs.columns.${experimentId}`       | `lolday.runs.columns.${experimentId}` |
| `_authed.runs.$expId.tsx`                   | `runs.status.${expId}`               | `lolday.runs.status.${expId}`         |
| `_authed.models._index.tsx` `DISMISSED_KEY` | (file-local const)                   | prefix the const value with `lolday.` |

Verify ThemeProvider's `storageKey` first — it's a component prop with a default; if the default is already `lolday-theme` we just rename to `lolday.theme` for consistency (dot separator matches all other lolday namespacing).

- [ ] **Step 1: Read each file to confirm the current key string.**

  ```bash
  grep -nE 'localStorage|storageKey|DISMISSED_KEY' \
    frontend/src/components/ThemeProvider.tsx \
    frontend/src/components/runs/RunsColumnPicker.tsx \
    frontend/src/routes/_authed.runs.\$expId.tsx \
    frontend/src/routes/_authed.models._index.tsx
  ```

  Capture each literal — they are what the implementer renames.

- [ ] **Step 2: Edit `frontend/src/components/ThemeProvider.tsx`.**

  Find the default value of the `storageKey` prop (likely in the props interface or function signature):

  ```typescript
  storageKey = "lolday-theme"; // or similar
  ```

  Change to:

  ```typescript
  // L-localstorage-ns: lolday. prefix on all owned keys for app-scoped storage.
  storageKey = "lolday.theme";
  ```

  If callers of `ThemeProvider` pass an explicit `storageKey` prop, update those too (most likely in `frontend/src/App.tsx` or `main.tsx`).

- [ ] **Step 3: Edit `frontend/src/components/runs/RunsColumnPicker.tsx`.**

  Replace both occurrences (lines 29 + 86):

  ```typescript
  `runs.columns.${experimentId}`;
  ```

  with:

  ```typescript
  `lolday.runs.columns.${experimentId}`;
  ```

- [ ] **Step 4: Edit `frontend/src/routes/_authed.runs.$expId.tsx`.**

  Replace both occurrences (lines 70 + 74):

  ```typescript
  `runs.status.${expId}`;
  ```

  with:

  ```typescript
  `lolday.runs.status.${expId}`;
  ```

- [ ] **Step 5: Edit `frontend/src/routes/_authed.models._index.tsx`.**

  Find the `DISMISSED_KEY` const (around line 131-135):

  ```typescript
  const DISMISSED_KEY = "models-dismissed"; // or similar
  ```

  Change to:

  ```typescript
  // L-localstorage-ns: lolday. prefix on all owned keys.
  const DISMISSED_KEY = "lolday.models.dismissed";
  ```

- [ ] **Step 6: Verify with a grep — every lolday-owned localStorage access is prefixed.**

  ```bash
  grep -RnE 'localStorage\.(get|set)Item' frontend/src/ | grep -v 'lolday\.' | grep -v 'i18nextLng'
  ```

  Expected: empty (i18next's `i18nextLng` is library-managed and intentionally excluded).

- [ ] **Step 7: Run the frontend test suite + build.**

  ```bash
  cd frontend && pnpm test --run
  cd frontend && pnpm build
  ```

  Expected: tests green; build clean.

- [ ] **Step 8: Commit.**

  ```bash
  git add frontend/src/components/ThemeProvider.tsx \
    frontend/src/components/runs/RunsColumnPicker.tsx \
    frontend/src/routes/_authed.runs.\$expId.tsx \
    frontend/src/routes/_authed.models._index.tsx
  git commit -m "fix(frontend): prefix every owned localStorage key with lolday. [L-localstorage-ns]"
  ```

---

## Task 15: [L-window-location] Replace `window.location.href` with `useNavigate()` in 3 route files

**Findings:** L-window-location (LOW). Recommended model: **sonnet** (3-file edit + manual smoke).

**Files:**

- Modify: `frontend/src/routes/_authed.datasets._index.tsx:93`
- Modify: `frontend/src/routes/_authed.detectors._index.tsx:156`
- Modify: `frontend/src/routes/_authed.jobs._index.tsx:216`

**Rationale:** Per D6, three sites use `window.location.href = \`/x/${id}\``for SPA-internal navigation.`window.location`triggers a full page reload, which (a) discards TanStack Router cache, (b) re-runs all loaders, (c) re-fetches all bundles.`useNavigate()` is the SPA-native alternative.

The fourth site (`_authed.runs.$expId.$runId.tsx:19`) uses `window.location.replace(\`/mlflow/#/...\`)`to redirect into the reverse-proxied MLflow UI; that's NOT a TanStack route and stays on`window.location.replace`. T17 covers its encoding.

- [ ] **Step 1: Edit `_authed.datasets._index.tsx:93`.**

  Find the line:

  ```typescript
  window.location.href = `/datasets/${d.id}`;
  ```

  At the top of the file, ensure the import:

  ```typescript
  import { useNavigate } from "@tanstack/react-router";
  ```

  In the component body, near the existing hooks:

  ```typescript
  const navigate = useNavigate();
  ```

  Replace the line:

  ```typescript
  navigate({ to: "/datasets/$id", params: { id: d.id } });
  ```

  (Match the route declaration's expected param shape — `_authed.datasets.$id.tsx` exposes `$id`. If the param name differs in the route file, use the matching name.)

- [ ] **Step 2: Edit `_authed.detectors._index.tsx:156`.**

  Same pattern:

  ```typescript
  window.location.href = `/detectors/${d.id}`;
  ```

  →

  ```typescript
  navigate({ to: "/detectors/$id", params: { id: d.id } });
  ```

- [ ] **Step 3: Edit `_authed.jobs._index.tsx:216`.**

  ```typescript
  window.location.href = `/jobs/${j.id}`;
  ```

  →

  ```typescript
  navigate({ to: "/jobs/$id", params: { id: j.id } });
  ```

- [ ] **Step 4: Verify with a grep — no remaining `window.location.href` in `_authed.*.tsx`.**

  ```bash
  grep -RnE 'window\.location\.href' frontend/src/routes/_authed.*.tsx
  ```

  Expected: empty.

- [ ] **Step 5: Run the frontend test suite + build.**

  ```bash
  cd frontend && pnpm test --run
  cd frontend && pnpm build
  ```

- [ ] **Step 6: Manual smoke (browser).**

  ```bash
  cd frontend && pnpm dev
  # Navigate to /datasets, /detectors, /jobs in the dev server.
  # Click a row — observe that the URL updates without a full reload
  # (network panel shows no document re-request).
  ```

  Expected: row click smoothly navigates; React DevTools shows the route component remounts without a full bundle reload.

- [ ] **Step 7: Commit.**

  ```bash
  git add frontend/src/routes/_authed.datasets._index.tsx \
    frontend/src/routes/_authed.detectors._index.tsx \
    frontend/src/routes/_authed.jobs._index.tsx
  git commit -m "fix(frontend): useNavigate for SPA-internal row clicks [L-window-location]"
  ```

---

## Task 16: [L-location-replace-encode] encodeURIComponent on MLflow redirect

**Findings:** L-location-replace-encode (LOW). Recommended model: **sonnet** (one-line edit + vitest test).

**Files:**

- Modify: `frontend/src/routes/_authed.runs.$expId.$runId.tsx:19`
- Test: `frontend/src/routes/_authed.runs.$expId.$runId.test.tsx` (new file)

**Rationale:** `_authed.runs.$expId.$runId.tsx:19` does:

```typescript
window.location.replace(`/mlflow/#/experiments/${expId}/runs/${runId}`);
```

`expId` and `runId` are pulled from route params (URL path). TanStack Router validates path-param shape via the route's `$id` template, but the values can still contain characters that aren't safe in the URL fragment context (e.g. `#` would terminate the fragment, `?` interpreted as a separate query in some parsers). `encodeURIComponent` percent-encodes them before interpolation — defense-in-depth for the rare case that a future schema allows special chars in run IDs.

- [ ] **Step 1: Write the failing test.**

  Create `frontend/src/routes/_authed.runs.$expId.$runId.test.tsx`:

  ```typescript
  import { describe, it, expect, vi, beforeEach } from "vitest";
  import { render } from "@testing-library/react";
  import { Route } from "./_authed.runs.$expId.$runId";

  describe("MLflow redirect — L-location-replace-encode", () => {
    beforeEach(() => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { replace: vi.fn() },
      });
    });

    it("percent-encodes expId and runId in the MLflow URL", () => {
      // Render the component with params containing special characters
      // (synthetic — real IDs are UUIDs, but the encode must be present
      // regardless).
      const Component = (Route as any).component ?? (Route as any).options?.component;
      if (Component) {
        // The component reads params via useParams from the route context;
        // we monkey-patch the params lookup for the assertion.
        vi.mock("@tanstack/react-router", async () => {
          const actual: any = await vi.importActual("@tanstack/react-router");
          return {
            ...actual,
            useParams: () => ({ expId: "exp #1", runId: "run/1?x" }),
          };
        });

        render(<Component />);
        // useEffect → window.location.replace fires.
        const replaceMock = (window.location.replace as any);
        expect(replaceMock).toHaveBeenCalledTimes(1);
        const calledWith = replaceMock.mock.calls[0][0];
        expect(calledWith).toContain("/mlflow/#/experiments/");
        expect(calledWith).toContain("exp%20%231");  // " " + "#" encoded
        expect(calledWith).toContain("run%2F1%3Fx");  // "/" + "?" encoded
      }
    });
  });
  ```

- [ ] **Step 2: Run test to verify failure.**

  ```bash
  cd frontend && pnpm test src/routes/_authed.runs.\$expId.\$runId.test.tsx --run
  ```

  Expected: FAILS — current code does not encode.

- [ ] **Step 3: Edit `frontend/src/routes/_authed.runs.$expId.$runId.tsx`.**

  Replace line 19:

  ```typescript
  window.location.replace(`/mlflow/#/experiments/${expId}/runs/${runId}`);
  ```

  with:

  ```typescript
  // L-location-replace-encode: percent-encode path segments before
  // interpolating into the URL fragment. /mlflow/ is the reverse-
  // proxied MLflow UI (NOT a TanStack route), so useNavigate doesn't
  // apply — but defense-in-depth encoding remains useful for any
  // future schema that allows special characters in IDs.
  window.location.replace(
    `/mlflow/#/experiments/${encodeURIComponent(expId)}/runs/${encodeURIComponent(runId)}`,
  );
  ```

- [ ] **Step 4: Run test to verify pass.**

  ```bash
  cd frontend && pnpm test src/routes/_authed.runs.\$expId.\$runId.test.tsx --run
  ```

  Expected: PASSES.

- [ ] **Step 5: Commit.**

  ```bash
  git add frontend/src/routes/_authed.runs.\$expId.\$runId.tsx \
    frontend/src/routes/_authed.runs.\$expId.\$runId.test.tsx
  git commit -m "fix(frontend): encodeURIComponent for MLflow redirect IDs [L-location-replace-encode]"
  ```

---

## Task 17: [L-samples-hostpath] Document samples hostPath as accepted tech debt

**Findings:** L-samples-hostpath (LOW). Recommended model: **sonnet** (doc-only).

**Files:**

- Modify: `docs/architecture.md` §10 (Known tech debt)

**Rationale:** `charts/lolday/templates/samples-pv.yaml` mounts the detector samples via a node-local `hostPath` pointing at `/mnt/lolday-samples` on server30. This is not portable across nodes — if lolday ever scales to a 2-node cluster, the samples PVC ROX claim cannot follow a workload pod to the second node. Per spec §6.6, this is **accepted tech debt** at the moment (single-node K3s, samples are TB-scale, mergerfs union mount source-of-truth lives on the host filesystem). The doc entry captures the decision so a future audit pass doesn't re-raise it.

**No code change.** This task is the bookkeeping that closes the finding ID.

- [ ] **Step 1: Locate `docs/architecture.md` §10.**

  ```bash
  grep -n '^## 10\. Known tech debt' docs/architecture.md
  ```

  Expected: a single line number (currently 473).

- [ ] **Step 2: Append a new tech-debt entry under §10.**

  Insert the following block at the end of §10 (just before `## 11. Common gotchas`):

  ```markdown
  ### L-samples-hostpath — samples PV uses node-local hostPath (accepted)

  `charts/lolday/templates/samples-pv.yaml` declares a `hostPath` PV at
  `/mnt/lolday-samples` on server30. The mergerfs union (over
  `/mnt/server14/dataset` NFS + local banks per `docs/operations.md`
  §NFS dataset sources) keeps detector samples as host-filesystem state.

  **Trade-off:** the chart cannot be redeployed onto a 2-node cluster
  without first replicating the union-mount setup on the second node.
  Today lolday is single-node K3s (`docs/architecture.md` §2.1); the
  hostPath is acceptable. A migration to ReadWriteMany NFS / S3 is in
  scope only if the cluster grows beyond one node.

  Audit ref: `docs/superpowers/specs/2026-05-12-security-hardening-design.md`
  §6.6, finding ID `L-samples-hostpath`. Decision captured in plan
  `docs/superpowers/plans/2026-05-14-security-hardening-p6-dos-cleanup.md`
  Task 17.

  ### H-26 connection-pool tech debt

  P6 H-26 set `db.py` `create_async_engine(pool_size=20, max_overflow=30)`.
  With 2 backend replicas, total checkout cap = 100 connections, exactly
  matching the Postgres default `max_connections`. **Scaling backend to
  3+ replicas requires a parallel bump in `postgresql.max_connections`**
  (chart values) and a Postgres restart. Tracked here so the requirement
  is not surprising; folded into the §10 audit per program acceptance
  gate (spec §11 item 4).
  ```

- [ ] **Step 3: Verify with a grep.**

  ```bash
  grep -F 'L-samples-hostpath' docs/architecture.md
  grep -F 'H-26 connection-pool' docs/architecture.md
  ```

  Expected: each returns the heading line.

- [ ] **Step 4: Commit.**

  ```bash
  git add docs/architecture.md
  git commit -m "docs(arch): record L-samples-hostpath and H-26 pool sizing as tech debt"
  ```

---

## P6 Done

After Task 17 lands, verify the whole phase end-to-end against spec §6.6 acceptance criteria.

- [ ] **Step A: Full backend test suite.**

  ```bash
  cd backend && uv run pytest -q 2>&1 | tail -5
  ```

  Expected: 773 baseline + new tests from T1, T2, T3, T4, T5, T6, T7, T8 ≈ 793–805 passed. No failures, no skips beyond pre-existing baseline.

- [ ] **Step B: helm lint (post-P6).**

  ```bash
  helm lint charts/lolday \
    --set redis.auth.password=test --set backend.fernetKeys=test \
    --set postgresql.auth.password=test --set mlflow.auth.password=test \
    --set mlflow.db.password=test --set harborAdminPassword=test \
    --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
    --set monitoring.postgresExporter.password=test
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. **Spec §6.6 acceptance criterion #4.**

- [ ] **Step C: `pnpm audit --prod` clean.**

  ```bash
  cd frontend && pnpm audit --prod
  ```

  Expected: 0 high / 0 critical. **Spec §6.6 acceptance criterion #5.**

- [ ] **Step D: Live `/health` rate-limit smoke (spec §6.6 acceptance criterion #1).**

  After `bash scripts/deploy.sh` ships the chart, fire 1000 concurrent invalid-JWT requests against the public surface and confirm backend stays up.

  ```bash
  # 1000 concurrent requests with a bogus JWT
  seq 1 1000 | xargs -P 50 -I{} curl -s -o /dev/null -w '%{http_code}\n' \
    -H 'Cf-Access-Jwt-Assertion: invalid.jwt.value' \
    https://lolday.connlabai.com/api/v1/jobs > /tmp/p6-step-d-codes.txt
  echo "code counts:"
  sort /tmp/p6-step-d-codes.txt | uniq -c

  # Backend pod still Running, no restarts
  kubectl -n lolday get pods -l app=backend -o wide
  kubectl -n lolday describe pod -l app=backend | grep -E 'Restart Count|State:|Ready:'

  # rate-limit counter for the 'health' prefix incremented? (not the same
  # endpoint, but confirms the metric path is alive — for the criterion's
  # exact phrasing the rate-limit hits would be on the JWT-failure path,
  # which doesn't have an IP-keyed limiter in P5; the 1000-req acceptance
  # is really about backend staying up + the JWT failure counter from P5
  # incrementing 1000 times)
  kubectl -n monitoring port-forward svc/kps-prometheus 9090:9090 &
  sleep 2
  curl -s 'http://localhost:9090/api/v1/query?query=increase(lolday_auth_failure_total[5m])' \
    | jq '.data.result[].value[1]'
  ```

  Expected: backend pod `Running`, restart count unchanged, `lolday_auth_failure_total` increased by ~1000 over the 5-minute window. No 5xx from backend.

- [ ] **Step E: Live MLflow streaming download smoke (spec §6.6 acceptance criterion #2).**

  Pre-stage a 500 MiB MLflow artifact (via `mlflow log_artifact` against a synthetic run owned by the operator).

  ```bash
  # Capture baseline memory
  kubectl -n lolday top pod -l app=backend

  # Download the 500 MiB artifact through the proxy (cookie-authed)
  curl -fSL -o /dev/null \
    --cookie "CF_Authorization=<operator JWT>" \
    https://lolday.connlabai.com/api/v1/runs/<run-id>/artifacts/download?path=large.bin &

  # While the download is in flight, observe backend memory
  watch -n 1 'kubectl -n lolday top pod -l app=backend'
  ```

  Expected: backend memory stays well below the 512 MiB limit throughout the download (peak likely 200-300 MiB; <50 MiB attributable to the stream itself). No OOMKill on the backend pod. The download completes successfully.

- [ ] **Step F: CSRF cross-origin smoke (spec §6.6 acceptance criterion #3).**

  ```bash
  curl -i -X POST https://lolday.connlabai.com/api/v1/jobs \
    -H 'origin: http://evil.example' \
    -H 'content-type: application/json' \
    -b "CF_Authorization=<operator JWT>" \
    -d '{}'
  ```

  Expected: `HTTP/2 403` with body containing `csrf check failed: Origin='http://evil.example' does not match Host='lolday.connlabai.com'`.

- [ ] **Step G: pre-commit on all files.**

  ```bash
  pre-commit run --all-files
  ```

  Expected: clean. **Do NOT use `--no-verify`.**

- [ ] **Step H: Cross-check finding IDs in commit history.**

  ```bash
  git log --oneline main..HEAD | grep -oE '\[[A-Z][^]]+\]' | tr ' ' '\n' | sort -u | tr -d '[]'
  ```

  Expected output (sorted unique set):

  ```
  H-26
  L-clone-bandwidth
  L-cloudflared-runas
  L-experiment-stats-lock
  L-frontend-pull-policy
  L-location-replace-encode
  L-localstorage-ns
  L-monitoring-quota
  L-promql-fstring
  L-registry-dead
  L-validator-size
  L-window-location
  L-ws-origin-check
  M-csrf
  M-mlflow-stream
  M-notify-semaphore
  M-reconciler-limit
  ```

  17 distinct IDs across 17 commits (T7 commit carries both `L-clone-bandwidth` and `L-validator-size` per D7). T17's commit is the doc-only entry without a finding-ID tag — that's expected; the audit-trail is the doc entry itself.

- [ ] **Step I: Open the PR.**

  Push the branch + `gh pr create --base main`. PR body must call out:
  - **No database migration** — P6 is a zero-migration phase. `audit_log` (P5) and earlier tables are unchanged.
  - **New middleware (`CSRFOriginMiddleware`)** — adds CSRF protection on `POST/PUT/PATCH/DELETE` for `/api/v1/*`. Fail-open on non-browser (CLI/service-token) traffic per D1. Cross-origin browser POST → 403.
  - **kubelet livenessProbe retargets `/livez` on `:8001`** — first deploy must wait for the chart upgrade to settle before kubelet trusts the new probe path. The existing readinessProbe stays on `/health:8000`.
  - **DB connection pool 50/pod × 2 replicas = 100 = Postgres `max_connections` cap.** Adding a third backend replica requires bumping `postgresql.max_connections`. Tracked in `docs/architecture.md` §10 (T17).
  - **localStorage prefix break (no migration)** — theme/column/dismiss preferences reset on first post-deploy visit. Announce on Spidey Service Alerts.
  - **Three new chart hardening items** — `imagePullPolicy: Always` on frontend (safe behind P4 digest pin), cloudflared `runAsUser: 65532`, ResourceQuota on `monitoring` ns.
  - **Dead-template removal** — `templates/registry.yaml` deleted; Harbor superseded it. Zero rendered resources change.
  - **Tech debt entries** — `L-samples-hostpath` and `H-26 connection-pool tech debt` documented in `docs/architecture.md` §10 per program acceptance gate (spec §11 item 4).

---

## Program-level acceptance gate (spec §11)

P6 is the program's final phase. After P6 squash-merges, verify the five program-level acceptance items from spec §11:

1. **All six phase plans merged.** P1, P2, P3, P4, P5 are all in `git log --grep='security(p'` history. P6 plan is this file; its squash-merge satisfies (1). Verify with:

   ```bash
   git log --oneline main --grep='security(p[0-9]):'  | head -10
   ```

   Expected: ≥ 6 distinct phase-tagged commits since 2026-05-12.

2. **Each phase's acceptance criteria verified in production deployment.** P1 / P2 / P3 / P4 / P5 each documented their own production verification at PR merge. P6's verification is "P6 Done" steps A–F above. Sign-off: each PR's "Test plan" checklist had ≥ 1 box checked in production by the operator.

3. **`pnpm audit --prod`, `uv pip audit` (or equivalent), `trivy image` (CRITICAL gate), `helm lint`, `pre-commit run --all-files` all clean.** Run the full sweep:

   ```bash
   cd /home/bolin8017/Documents/repositories/lolday

   # pnpm audit
   ( cd frontend && pnpm audit --prod )

   # uv pip audit (or `uv pip list --outdated` if pip-audit isn't installed)
   ( cd backend && uv run pip-audit -r <(uv pip compile pyproject.toml 2>/dev/null) ) 2>&1 | tail -20 \
     || echo 'pip-audit unavailable; fall back to GHA workflow check.'

   # trivy on each pushed image (operator)
   for img in harbor.lolday.svc:80/lolday/lolday-backend:v0.22.1 \
              harbor.lolday.svc:80/lolday/lolday-frontend:v0.22.1; do
     trivy image --severity CRITICAL --exit-code 1 "$img" || echo "FAIL on $img"
   done

   # helm lint (canonical 9-key chain)
   helm lint charts/lolday \
     --set redis.auth.password=test --set backend.fernetKeys=test \
     --set postgresql.auth.password=test --set mlflow.auth.password=test \
     --set mlflow.db.password=test --set harborAdminPassword=test \
     --set cloudflare.tunnelToken=test --set grafana.adminPassword=test \
     --set monitoring.postgresExporter.password=test

   # pre-commit
   pre-commit run --all-files
   ```

   Expected: every command exits 0. **No `--no-verify`, no skipped categories.**

4. **A follow-up audit pass confirms each finding is closed; remaining items moved to `docs/architecture.md` §10 tech-debt with explicit reasoning.** P6 T17 added entries for `L-samples-hostpath` and `H-26 connection-pool`. The follow-up audit is a separate session: replay the 85 findings against the post-P6 codebase, mark each as `closed` (commit reference) or `accepted tech debt` (§10 entry). Open a new PR titled `audit(post-2026-05-12): close-out pass`. The PR body links each finding ID to its closure evidence.

5. **`docs/postmortems/` gains a "what we learned" entry for the 2026-05-12 audit.** Create `docs/postmortems/2026-05-12-security-audit-program.md` (timestamped per the audit date, not P6 ship date). Outline:
   - The five root-cause themes (Theme A: god-node backend; Theme B: paper-thin tenant isolation; Theme C: BOLA via MLflow proxy; Theme D: secret-lifecycle drift; Theme E: image supply-chain) and how each was structurally closed.
   - The breaking-change inventory (spec §7) and which windows succeeded / required intervention.
   - The cross-phase patterns that were useful (single-Counter-per-finding metric discipline, `BACKEND_ERRORS{stage=...}` as universal failure bus, Pydantic `field_validator` at boot as a CrashLoopBackOff-first defense).
   - The trade-offs accepted as tech debt (§10 entries).
   - **One full-program ratio metric:** "85 findings → 18 in P6 → 17 tasks. 6 phase plans, 5 squash-merges, 1 program."

   **This is a post-program retrospective document** — author it after the post-P6 follow-up audit (item 4) concludes. P6 PR body must include a reminder TODO for this; it does not block P6 ship.

**Declaration**: After items 1–3 are mechanically green and items 4–5 are scheduled, the security hardening program (2026-05-12 → P6 ship) is **complete**. Subsequent security work continues as ad-hoc PRs against named findings, not phases.

---

## Notes for the implementer

- **`asyncio.Semaphore._value` is a private attribute** (T2 test, T3 production code, T3 test). Python's `asyncio.Semaphore` exposes no public `try_acquire()`; reading `._value` is the standard idiom (see CPython `asyncio/locks.py` source — `BoundedSemaphore.release` reads `._value` too). The risk is breakage on a CPython internal change; mitigation: pin Python version in `pyproject.toml` (already in place via `python = ">=3.12,<3.13"` per `backend/pyproject.toml`). When CPython removes the attribute, replace with `Semaphore.locked()` (returns True iff zero permits) + a sibling counter to track saturation; the swap is mechanical.
- **`r.content` is the hot test on T2.** `httpx.AsyncClient.stream(...)` returns a context manager — calling `.aread()` inside it consumes the stream to bytes; the _bug_ the spec is closing is using `await c.get(url); return Response(content=r.content, ...)` which buffers. The T2 test asserts the function was rewritten to use `c.stream(...)` (mock-observable), not the absence of `.content` (which a buggy port back to buffering would still pass).
- **CSRF middleware is OUTSIDE FastAPI's dep injection.** Starlette middleware sees the request before any route's `Depends(current_active_user)` runs. This is the right place — CSRF gating must be a pre-auth check (a forged request shouldn't even trigger user lookup). The flip side: errors logged inside the middleware can't reach the request's logger context (no `request_id` yet). Mitigation: log the path + minimal header set only.
- **Reconciler scan-cap behaviour shift.** Today, the reconciler scans ALL non-terminal rows per iteration. After T4, it scans the oldest 200 (or all, if fewer). On a queue of < 200 rows the behaviour is identical. On a queue > 200, the newest rows wait one iteration (~10s by default). Counter rate `> 0` is the operator signal that the queue is growing faster than reconciliation.
- **DB pool tuning is a `create_async_engine` argument change, not a Postgres-side change.** Postgres `max_connections` remains at the default 100; bumping backend's pool to 50 per pod × 2 replicas exactly uses that budget. A third replica requires Postgres-side `max_connections=150` + restart. T17 captures this as documented tech debt.
- **`window.location.replace` versus `useNavigate` on T16.** TanStack Router doesn't manage `/mlflow/` — that's a reverse-proxied SPA with its own hash router. `useNavigate({ to: "/mlflow/..." })` would treat it as an internal route, fail to match, and 404 inside lolday. `window.location.replace` is the right primitive for cross-SPA navigation; the encoding is the defense.
- **i18next localStorage keys are out of scope for T14.** i18next stores `i18nextLng` via its own `LocalStorageBackend`. Renaming the key requires changing `detection.lookupLocalStorage` in `frontend/src/i18n/index.ts:19-20` — touching this can race with the library's first-load detection. Out of scope; doc the exclusion in PR body.
- **Per-task TDD discipline.** T1, T2, T3, T4, T5, T6, T7, T8, T13, T15 (the test file is named in §13's Step 1), T16 are backend + frontend code with paired tests — TDD required (failing test first). T9, T10, T11, T12, T14, T17 are chart / doc edits with no automated test; verification is helm-lint + render-check + manual smoke (T17 is grep-only).
- **Model selection per task** (recommended; pass via `--model` to subagent):
  - **sonnet** — T3, T4, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17 (single-file or single-surface edits, paired test)
  - **opus** — T1 (multi-surface — dep wiring + db engine + chart probe), T2 (rewrites buffer → stream + semaphore + multi-mock test), T5 (new middleware + 10-case test matrix)

---

## Self-review (writing-plans skill)

**Spec coverage — every P6 finding from spec §6.6 maps to a task:**

| Finding                   | Task               |
| ------------------------- | ------------------ |
| H-26                      | T1                 |
| M-mlflow-stream           | T2                 |
| M-notify-semaphore        | T3                 |
| M-reconciler-limit        | T4                 |
| M-csrf                    | T5                 |
| L-experiment-stats-lock   | T6                 |
| L-clone-bandwidth         | T7                 |
| L-validator-size          | T7 (folded per D7) |
| L-promql-fstring          | T8                 |
| L-frontend-pull-policy    | T9                 |
| L-cloudflared-runas       | T10                |
| L-monitoring-quota        | T11                |
| L-registry-dead           | T12                |
| L-ws-origin-check         | T13                |
| L-localstorage-ns         | T14                |
| L-window-location         | T15                |
| L-location-replace-encode | T16                |
| L-samples-hostpath        | T17                |

18 spec findings → 17 implementation tasks (1:0.94 mapping; T7 covers both clone-bandwidth + validator-size — see D7). Spec-level acceptance criteria (§6.6) all traceable:

| Spec acceptance                                                                                     | Plan check                                      |
| --------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 1. 1000 concurrent invalid-JWT requests → backend stays up; rate-limit counter reflects cap         | P6 Done Step D                                  |
| 2. 500 MiB MLflow artifact with 512 MiB pod limit → no OOMKill                                      | T2 + P6 Done Step E                             |
| 3. Cross-origin `fetch('/api/v1/jobs', {method:'POST'})` from `http://evil` → 403 (Origin mismatch) | T5 + P6 Done Step F                             |
| 4. `helm lint charts/lolday` clean                                                                  | T1–T12 helm-lint steps + P6 Done Step B         |
| 5. `pnpm audit --prod` clean                                                                        | Pre-flight `pnpm audit --prod` + P6 Done Step C |

**Placeholder scan:**

- `<run-id>` / `<operator JWT>` / `<TEST_USER_UUID>` in P6 Done Steps E + F — operator-supplied at smoke-test time. Same pattern P5 / P4 Done used.
- No `TBD` / `implement later` / `add appropriate error handling` markers.
- No `<REV>` / `<...>` placeholders inside committed code — every code edit shows complete content.

**Type consistency:**

- `_MLFLOW_STREAM_SEM: asyncio.Semaphore` (T2) and `_NOTIFY_SEM: asyncio.Semaphore` (T3) — module-level, typed identically.
- `RECONCILER_SCAN_TRUNCATED_TOTAL.labels(kind=...)` — `kind` keyword consistent across both call sites (`_scan_jobs` → `"job"`, `_scan_builds` → `"build"`). Cardinality bounded at 2.
- `BACKEND_ERRORS.labels(stage=...)` — existing P3 / P4 / P5 stage labels (`discord_notify`, `reconcile_*`, ...) augmented with new `discord_notify_dropped` (T3). String values are all consistent (snake_case).
- `_origin_matches_host(origin: str, host: str) -> bool` (T5) — no `None` path (caller guards both args present).
- `_scan_jobs` / `_scan_builds` signatures (`session, limit: int = RECONCILER_SCAN_LIMIT`) — symmetric.
- `useNavigate({ to: "/x/$id", params: { id: ... } })` (T15) — three call sites, identical shape modulo the route literal.

**Known fragilities:**

- **T2 streaming inside FastAPI is mock-fragile.** The `_FakeAsyncClient` in T2 Step 1 monkey-patches the symbol `httpx.AsyncClient` at the module level. If a future test or middleware imports `httpx.AsyncClient` differently (`from httpx import AsyncClient`), the patch misses. Mitigation: patch the symbol via the import location in `experiments_proxy` (`monkeypatch.setattr("app.routers.experiments_proxy.httpx.AsyncClient", _FakeAsyncClient)`) — already done in the test.
- **T3 `._value` private attribute.** Discussed in "Notes for the implementer" above. Locked decision.
- **T4 `submitted_at` ordering on aiosqlite.** Aiosqlite's `DateTime` column ordering matches Postgres's `TIMESTAMP WITH TIME ZONE` ordering byte-for-byte (ISO 8601 sortable), so the test's seed pattern produces the same order in both backends. Verified against P5 audit_log tests (same pattern).
- **T5 CSRF middleware order.** Discussed — LIFO; `BodySizeLimitMiddleware` is outer, `CSRFOriginMiddleware` is inner. If a future middleware (e.g. tracing) is added, the ordering review must check that CSRF runs before any code that touches request state.
- **T14 localStorage break is operator-visible.** Users lose theme / column / dismiss state. Mitigation: PR body announces on Spidey Service Alerts; the cost of the break (≤ 5 users, 30 seconds of re-picking) is bounded.
- **T17 doc edit is grep-verifiable but not test-verifiable.** Mitigation: P6 Done Step H's commit-history grep would catch a missing `L-samples-hostpath` entry by the absence of the T17 commit (no finding-ID tag, but the commit message ` docs(arch): record L-samples-hostpath` is greppable).

**Deferred (NOT in P6):**

- `CSRF_REJECTED_TOTAL{reason}` counter + alert. Spec doesn't call for it; P7 follow-up if rejection rate becomes interesting.
- Migration code for the localStorage rename. Per D5, break-no-migrate is the locked decision.
- Bumping `postgresql.max_connections`. Captured as tech debt in T17 §10 entry; only matters when scaling beyond 2 backend replicas.
- Audit-log retention policy (`pg_partman` partitioning + 365-day TTL). Carried over from P5 deferral; no acceptance criterion in P6 either.
- Reconciler scan cap as a settings-tunable. `RECONCILER_SCAN_LIMIT = 200` is module-level constant; making it `settings.RECONCILER_SCAN_LIMIT` is YAGNI absent a tuning need.
- Per-finding alert rules for `RECONCILER_SCAN_TRUNCATED_TOTAL`. Spec says no rule in this phase.
- Touching i18next `i18nextLng`. Library-internal; out of scope.

---

## Estimated effort breakdown

17 tasks, single-engineer (sonnet/opus mix per recommendation), TDD on backend tasks:

| Chain                                   | Tasks                     | Effort                                  |
| --------------------------------------- | ------------------------- | --------------------------------------- |
| DoS hardening (backend code)            | T1, T2, T3, T4            | ~4 hrs                                  |
| CSRF middleware                         | T5                        | ~2.5 hrs                                |
| Backend hygiene (lock + clone + promql) | T6, T7, T8                | ~1.5 hrs                                |
| Chart hardening                         | T9, T10, T11, T12         | ~1.5 hrs                                |
| Frontend hardening                      | T13, T14, T15, T16        | ~2.5 hrs                                |
| Docs (accepted tech debt)               | T17                       | ~0.5 hr                                 |
| Deploy + verify + PR                    | P6 Done                   | ~2 hrs                                  |
| Program acceptance gate                 | (spec §11)                | ~2 hrs (separate session for items 4-5) |
| **Total in this PR**                    | **17 tasks + verify**     | **~14.5 hrs**                           |
| **Total including post-program**        | + post-audit + postmortem | **~18.5 hrs**                           |

Within spec's "~1–2 weeks" upper bound (2–3 working days of focused work for the P6 PR itself; post-program follow-up is a separate ≤ 1-day session).

P6 closes the security hardening program. Subsequent security work continues as ad-hoc PRs against named findings — not as another phase.
