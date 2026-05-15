# Test Architecture Redesign — Design Specification

> **Created 2026-05-15**. Trigger: operator concerned that recent high-velocity
> PR activity — P5/P6 security hardening (88 findings across six phases),
> RJSF v5→v6 frontend migration, four consecutive chart hotfixes
> (#152 / #150 / #179 / #181), supply-chain hardening (SLSA L3 + cosign +
> Kyverno) — may have introduced silent regressions that the existing test
> suite cannot catch. The operator asked for a production-grade
> regression-prevention test architecture covering every layer
> (backend / frontend / Helm chart / scripts), with a phased Roadmap that a
> solo maintainer can land.

> **This spec answers:** what test pyramid, tool stack, directory layout,
> CI/CD pipeline, regression matrix, and refactoring proposals will give
> lolday a long-term-maintainable, automation-friendly, production-grade
> test architecture; in what order to land each component (Phase 0–4 plus
> an optional Phase 5); and what regression-risk class each phase reduces.

## 1. Motivation

### 1.1 Current test state

| Layer      | What exists                                                                                                                             | Key gaps                                                                                                                                                                               |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend    | 96 `test_*.py` files; pytest + `aiosqlite` in-memory; autouse mocks for K8s / Volcano CRD / MLflow / Redis / Discord; `respx` for httpx | No API contract test against `/openapi.json`; no Alembic up/down round-trip; no property-based; no real-Postgres parity test; coverage threshold absent                                |
| Frontend   | 63 vitest unit + 23 playwright E2E specs; `vitest.config.ts` coverage scope is `src/lib/` + `src/hooks/` only                           | Playwright is **commented out** in `frontend.yml`; `src/components/` and `src/routes/` not measured; no MSW; no visual regression; no role-based E2E (single `AUTH_DEV_EMAIL` persona) |
| Helm chart | `helm lint` + `helm template`; seven shell smoke scripts in `tests/phase7/`                                                             | Zero `helm-unittest` coverage; `tests/phase7/` is not triggered by CI; no `kubeconform` / `kyverno-cli test`                                                                           |
| CI         | Single-job sequential per workflow; pre-commit is the lint SSOT                                                                         | No matrix sharding; no contract tier; no slow-tier (testcontainers / E2E) workflow; no flaky tracker                                                                                   |
| Scripts    | None                                                                                                                                    | `deploy.sh`, `build-helpers.sh`, `recover-harbor.sh` have no tests; recent fixes (#184 Python heredoc, #185 cosign-sign) would have been caught by `bats` units                        |

### 1.2 Risk surface

Top-10 high-risk modules ranked by `(30-day churn × fan-in × postmortem
involvement)`:

| #   | Module                                     | LOC | Why high-risk                                                                                        |
| --- | ------------------------------------------ | --- | ---------------------------------------------------------------------------------------------------- |
| 1   | `backend/app/routers/jobs.py`              | 920 | 21 intra-app imports; 26 commits in last 30 d; glue for forms → validator → MLflow → K8s → notify    |
| 2   | `backend/app/reconciler/jobs.py`           | 496 | Six-state machine; reads Volcano + events + timeouts; calls MLflow + Discord                         |
| 3   | `backend/app/services/mlflow_client.py`    | 321 | Module-level lazy `AsyncClient`; recent leak fix (P6 spec `2026-05-12-mlflow-client-async-leak-fix`) |
| 4   | `backend/app/reconciler/fifo_scheduler.py` | 164 | GPU free-count contract with Prometheus; upstream Volcano bug #5044 still open                       |
| 5   | `backend/app/services/build.py`            | 417 | BuildKit manifest + env var injection; Fernet encryption (P3 H-18)                                   |
| 6   | `backend/app/routers/experiments_proxy.py` | 465 | Per-user MLflow ACL; artefact stream; path-traversal block (P6 M-mlflow-stream)                      |
| 7   | `backend/app/auth/cf_access.py`            | 323 | Cloudflare JWT verify; cache TTL; dev-mode bypass; P5 H-27 JWT-shape fix                             |
| 8   | `backend/app/models/job.py`                | 176 | `JobStatus` enum totality assertion; `ResourceProfile` GPU mapping                                   |
| 9   | `backend/app/services/gpu_signal.py`       | 260 | Prometheus query; DCGM unit gotcha (`feedback_dcgm_metric_units_mib`)                                |
| 10  | `backend/app/reconciler/build_finalize.py` | 262 | Detector version from manifest; image digest pin                                                     |

Past incidents the new tests would have caught (or now must continue to
catch):

- **2026-04-21 Prometheus WAL corruption** (storage-layer infra regression)
  — Helm chart E2E covering PVC subPath + dead-man's-switch alert.
- **88-finding security hardening (P1–P6)** — each finding ID has a
  corresponding test today; the new contract + heavy tier locks them.
- **PR #184 Python heredoc bug in `build-helpers.sh`** — `bats` smoke
  - Python `scripts/lib/` unit would have caught.
- **`feedback_helm_upgrade_state_carry`** — Helm upgrade carry-over values;
  `chart-e2e.yml` upgrade/rollback smoke covers.

### 1.3 Why now

- PR velocity (40+ merged PRs in the last 30 days) makes hand-verification
  untenable.
- Recent dependency bumps (RJSF v5→v6, ESLint 10, TypeScript 6) shipped
  without behavioural coverage on `src/components/` or `src/routes/`.
- The 2026-05-14 public-repo flip lifts the GHA cost ceiling: every minute
  on `ubuntu-24.04` is free, so the slow tier is no longer budget-bound.
- The security program closeout means 88 finding-specific tests now exist
  and **must remain green forever**; a regression net is the only sustainable
  way to enforce that.

## 2. Goals & Non-goals

### 2.1 Goals

- **Two-tier CI**: PR fast tier ≤ 4 min wall clock; `main` + nightly slow
  tier ≤ 25 min.
- **Seven-layer pyramid**: static / unit / integration / contract / heavy
  integration / E2E / smoke.
- **Mainstream tooling only** (per project `CLAUDE.md` §Mainstream practices
  first): `testcontainers`, `schemathesis`, `helm-unittest`, `kubeconform`,
  `kyverno-cli`, `playwright`, `MSW`, `hypothesis`, `k3d`, `bats`.
- **Selective testing**: `dorny/paths-filter` drives per-PR test triggering
  so PRs run only what they touch (plus mandatory smoke).
- **Flaky discipline**: ≤ 1 % failure-rate SLO; 14-day fix / 21-day delete;
  `flaky_tracked` marker requires linked GitHub issue.
- **Regression coverage map** for the top-10 risk modules with explicit
  per-test-type scenario lists.
- **Long-term maintenance ritual**: per-PR test requirement; quarterly
  retrospective; framework upgrade SOP; test code path-scoped ownership.

### 2.2 Non-goals

- Chaos engineering (toxiproxy / chaos-mesh / litmus) — moved to optional
  Phase 5; only triggered by a real chaos incident.
- Pact-style contract testing on top of `schemathesis` — `schemathesis`
  already covers OpenAPI fuzzing; second framework adds maintenance burden
  without unique value.
- Visual regression via Percy / Chromatic SaaS — `playwright` built-in
  snapshot is sufficient and avoids external SaaS lock-in.
- Self-hosted GitHub Actions runners (per
  `.claude/rules/github-actions.md` §Forbidden additions).
- Rewriting any of the 96 backend / 63 vitest / 23 playwright tests — they
  are preserved verbatim; only file paths change during the directory
  reorg.
- Per-PR mutation testing — `mutmut` is weekly only, since one run takes
  ~20 minutes per module.

## 3. Strategy: Hybrid two-tier CI with mainstream tooling

### 3.1 Why two-tier

A single-tier "everything every PR" pipeline forces a hard trade between
PR latency and test fidelity. The two-tier split lets PRs stay fast and
slow tests still happen every merge:

- **PR fast tier** (every PR, every push): static / unit / integration /
  contract, all in-memory or stubbed. Wall clock ≤ 4 min. **Required
  status check** — blocks merge if red.
- **Slow tier** (every `main` push and nightly cron): heavy integration
  (testcontainers) and E2E (k3d). Wall clock ≤ 25 min.
  **Informational** — runs post-merge, fix-forward.

The two-tier model is mainstream practice (Kubernetes, Prometheus,
Grafana, Vault all use it).

### 3.2 Why mainstream tooling

Per `CLAUDE.md` §Mainstream practices first: each tool below is the
dominant choice in its niche, with active community, official docs, and a
Dependabot ecosystem.

| Niche                           | Chosen tool                   | Why                                                                                |
| ------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------- |
| Python API contract / fuzz      | `schemathesis`                | Auto-derives property tests from `/openapi.json`; widely used in FastAPI ecosystem |
| Real-service Python integration | `testcontainers-python`       | Java / Python / Go all have official libs; CNCF-ecosystem standard                 |
| Helm chart unit                 | `helm-unittest`               | The de facto Helm plugin (1.2k+ ★); used by Bitnami / cert-manager / others        |
| K8s manifest static analysis    | `kubeconform` + `kyverno-cli` | CNCF-adjacent; both run in < 1 s without a cluster                                 |
| Frontend API mocking            | `MSW`                         | Vitest + React ecosystem dominant; intercepts at network layer, not module         |
| Bash unit                       | `bats-core`                   | The standard bash test framework                                                   |
| K8s ephemeral cluster (CI)      | `k3d`                         | Faster boot than `kind`; supports Helm install end-to-end                          |
| Frontend a11y                   | `@axe-core/playwright`        | Industry standard; integrates with existing playwright                             |
| Python property-based           | `hypothesis`                  | Python's de facto property-based framework                                         |

### 3.3 Alternatives considered

| Strategy                           | Description                                                                                              | Verdict                                                                                                                                                                        |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **A: in-memory only**              | Keep aiosqlite + autouse mocks; add `helm-unittest` and reactivate playwright; no testcontainers, no k3d | **Rejected** — leaves `aiosqlite` ↔ Postgres drift latent (JSONB query, FK cascade, advisory lock, deadlock detection); `services/mlflow_client.py` API drift cannot be caught |
| **C: full real-services + chaos**  | testcontainers everywhere; k3d for every PR; toxiproxy; visual regression in CI                          | **Rejected** — PR latency exceeds 30 min; toxiproxy adds expertise burden inappropriate for a one-maintainer team                                                              |
| **B: hybrid two-tier (this spec)** | PR fast tier in-memory + contract; slow tier real-services on `main` + nightly                           | **Chosen** — preserves PR latency, gives slow-tier the fidelity that A lacks, sidesteps C's maintenance burden                                                                 |

## 4. Test Pyramid & Tool Stack

### 4.1 The pyramid

```
                      Smoke (post-deploy)              — existing tests/phase7/ + post-deploy probes
                            ▲
                          E2E (slow)                   — reactivate playwright + k3d ephemeral cluster
                            ▲
                  Heavy integration (slow)             — NEW: testcontainers (Postgres / MLflow / MinIO)
                            ▲
                     Contract (fast)                   — NEW: schemathesis + helm-unittest + kubeconform
                            ▲
                  Integration (fast)                   — existing aiosqlite + respx + new MSW
                            ▲
                       Unit (fast)                     — existing + hypothesis on enum / state-machine invariants
                            ▲
                       Static (pre-commit)             — existing + kubeconform + kyverno-cli validate
```

### 4.2 Per-layer responsibility and tool stack

| Layer             | Trigger                 | Target time | Backend                                                                                  | Frontend                                                | Helm / Infra                                                          |
| ----------------- | ----------------------- | ----------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------- |
| Static            | pre-commit + `lint.yml` | < 30 s      | ruff, mypy                                                                               | prettier, eslint                                        | helm lint (existing); **kubeconform**, **kyverno-cli validate** (NEW) |
| Unit              | PR fast                 | < 2 min     | pytest + **hypothesis**; no DB, no HTTP                                                  | vitest + React Testing Library                          | —                                                                     |
| Integration       | PR fast                 | < 4 min     | pytest + aiosqlite + respx + fakeredis (existing)                                        | vitest + **MSW**                                        | —                                                                     |
| Contract          | PR fast                 | < 3 min     | **schemathesis** against running FastAPI; respx replay tape for MLflow shape             | openapi-ts schema-drift guard                           | **helm-unittest**, kubeconform, kyverno-cli test                      |
| Heavy integration | `main` + nightly        | < 15 min    | **testcontainers** (Postgres / MLflow / MinIO); **kubernetes-fake-client** (Volcano CRD) | full vitest including `src/components/` + `src/routes/` | —                                                                     |
| E2E               | `main` + nightly        | < 25 min    | playwright API checks                                                                    | playwright UI flows                                     | **k3d** ephemeral cluster + `helm install`                            |
| Smoke             | post-deploy             | manual      | curl `/healthz` `/ready`                                                                 | curl `/`, `kubectl rollout status`                      | existing `tests/phase7/` + new deploy-probe checklist                 |

### 4.3 Delta from current state

- **Add**: `schemathesis`, `helm-unittest`, `kubeconform`, `kyverno-cli`,
  `testcontainers-python`, `kubernetes-fake-client`, `hypothesis`, `MSW`,
  `k3d`, `bats-core`, `@axe-core/playwright`.
- **Modify**: `frontend.yml` reactivates playwright; `backend.yml` splits
  into `backend-fast.yml` and `backend-slow.yml`.
- **Preserve**: every existing test file. Reorg only moves them into the
  new directory tree (§5).
- **Reject**: toxiproxy / chaos-mesh / litmus (Phase 5 only); Percy /
  Chromatic SaaS; second contract-test framework (Pact); per-PR mutation
  testing.

## 5. Directory Layout, Naming, Fixtures

### 5.1 Target directory tree

```
backend/tests/
├── unit/                       # pure functions, no DB / no HTTP (< 50 ms each)
│   ├── auth/                   # test_cf_access_jwt_shape.py
│   ├── models/                 # test_job_status_state_machine.py, test_resource_profile_enum_totality.py
│   ├── services/               # test_job_spec_builder.py, test_build_args_redact.py
│   └── invariants/             # hypothesis-driven
├── integration/                # aiosqlite + autouse mocks (existing default)
│   ├── routers/
│   ├── reconciler/
│   ├── services/
│   └── migrations/             # existing + new test_migrations_upgrade_downgrade_roundtrip.py
├── contract/                   # NEW
│   ├── openapi/                # schemathesis against running FastAPI
│   ├── mlflow/                 # respx replay locks MLflow REST shape
│   ├── volcano/                # kubeconform on rendered vcjob manifests
│   ├── harbor/                 # Harbor v2 robot / repo / tag API shape
│   └── discord/                # webhook embed payload shape
├── heavy/                      # NEW: testcontainers (slow tier)
│   ├── postgres/
│   ├── mlflow/
│   ├── minio/
│   └── k8s_fake/               # kubernetes-fake-client; full Volcano CRD lifecycle
├── factories/                  # NEW: polyfactory — replaces 850-line inline factories in conftest
├── fixtures/                   # static data (existing sample_dataset.csv etc.)
│   ├── manifests/
│   ├── datasets/
│   ├── mlflow/recorded/        # NEW: respx replay tapes
│   └── secrets/
├── conftest.py                 # SPLIT: only auth client + db session + k8s base stub (< 200 lines)
└── conftest_helpers/           # shared helpers (not a conftest — avoids autouse pollution)

frontend/tests/
├── unit/
│   ├── lib/, hooks/, widgets/  # existing
│   └── components/             # NEW: now in coverage scope
├── integration/                # NEW: vitest + MSW
│   ├── routes/
│   └── forms/
├── contract/                   # NEW
│   └── schema_gen_drift.test.ts  # asserts handstitched fields exist in current /openapi.json
├── e2e/                        # existing 23 specs reorganised by feature
├── visual/                     # NEW: playwright screenshot snapshots
├── helpers/                    # existing + new page object models
└── mocks/                      # NEW: MSW
    ├── handlers.ts
    └── server.ts

charts/lolday/
├── tests/                      # NEW: helm-unittest
│   ├── backend_deployment_test.yaml
│   ├── networkpolicy_test.yaml
│   ├── kyverno_policy_test.yaml
│   ├── monitoring_alertrules_test.yaml
│   ├── alertmanagerconfig_test.yaml
│   └── pss_test.yaml
└── values-test.yaml            # NEW: minimal renderable values (monitoring off, fake secrets)

tests/
├── phase7/                     # existing shell smoke (rename optional → smoke/)
├── e2e_chart/                  # NEW: k3d-based slow tier
│   ├── deploy_smoke.sh
│   └── upgrade_rollback.sh
└── perf/                       # NEW (Phase 5 optional): k6 load scenarios
```

### 5.2 Naming conventions

| Scope            | Rule                                                      | Example                                                                     |
| ---------------- | --------------------------------------------------------- | --------------------------------------------------------------------------- |
| Backend file     | `test_<area>_<behavior>.py`, `area` maps to `app/<area>/` | `test_routers_jobs_dispatch_owner_ref.py`                                   |
| Backend function | `test_<scenario>_<expected>`                              | `test_create_job_invalid_param_returns_422`                                 |
| Parametrize      | Required; never copy-paste                                | `@pytest.mark.parametrize("status", [QUEUED, RUNNING, FAILED])`             |
| Frontend unit    | `<Component>.test.tsx` colocated with source              | `JobSubmitForm.test.tsx`                                                    |
| Frontend E2E     | `<feature>.spec.ts` under feature folder                  | `e2e/jobs/job-submit-validation-error.spec.ts`                              |
| helm-unittest    | `<resource>_test.yaml`, one per `templates/<subdir>/`     | `tests/networkpolicy_test.yaml`                                             |
| Pytest marker    | Limited to cross-layer behaviour                          | `@pytest.mark.heavy`, `@pytest.mark.contract`, `@pytest.mark.flaky_tracked` |

### 5.3 Fixture / mock-data strategy

**Three-layer separation**

- Static data → `tests/fixtures/` (JSON / CSV / recorded tape).
- Object generation → `tests/factories/` using `polyfactory`, replacing the
  ~850 lines of inline factories currently inside `conftest.py`
  (`_make_user`, `seed_user`, etc.).
- Request fixtures → per-layer `conftest.py`: integration owns autouse
  mocks; heavy owns testcontainers session-scoped lifecycle; contract owns
  schemathesis app + respx replay tapes.

**Split the 850-line `backend/tests/conftest.py`**

The current conftest has 30 commits in 30 days; every change risks
autouse cascade. Target:

| File                                    | Contents                                                          |
| --------------------------------------- | ----------------------------------------------------------------- |
| `backend/tests/conftest.py`             | auth client + db session + k8s base stub (< 200 lines)            |
| `backend/tests/integration/conftest.py` | autouse MLflow mock (~400 lines), fakeredis, respx defaults       |
| `backend/tests/heavy/conftest.py`       | testcontainers Postgres / MLflow / MinIO session-scoped lifecycle |
| `backend/tests/contract/conftest.py`    | schemathesis app loader; respx replay-tape loader                 |

**MLflow mocking is layered**

- `integration/`: keep autouse mock; fast and adequate.
- `heavy/mlflow/`: real MLflow image via testcontainers; **no mock**.
- `contract/mlflow/`: respx replay tapes serialised in
  `fixtures/mlflow/recorded/`.

**Frontend MSW**

- `mocks/handlers.ts` defines the centrally-registered handlers
  (`/api/v1/jobs`, `/api/v1/detectors`, etc.).
- Tests may locally override via `server.use(rest.post(...))`.
- E2E does **not** use MSW; it runs against the real backend in k3d.

**Helm-unittest shared values**

- `charts/lolday/values-test.yaml` carries fake secrets and disables
  monitoring sub-charts. It exists only for `helm-unittest` rendering and
  never pollutes `values.yaml`.

## 6. CI/CD Pipeline Design

### 6.1 Workflow inventory

| Workflow                                    | Tier                          | Trigger                        | What it runs                                                        | Required for merge? |
| ------------------------------------------- | ----------------------------- | ------------------------------ | ------------------------------------------------------------------- | ------------------- |
| `lint.yml`                                  | static                        | every PR / push                | pre-commit (existing) + kubeconform + kyverno-cli validate          | ✅                  |
| `backend-fast.yml`                          | unit + integration + contract | PR / push, paths `backend/**`  | `pytest -n auto -m "not heavy"` + schemathesis fast suite           | ✅                  |
| `backend-slow.yml`                          | heavy integration             | push `main` + nightly          | `pytest -m heavy` with testcontainers                               | ❌ informational    |
| `frontend-fast.yml`                         | unit + integration + contract | PR / push, paths `frontend/**` | typecheck + vitest unit + vitest+MSW integration + schema-gen-drift | ✅                  |
| `frontend-slow.yml`                         | E2E + visual                  | push `main` + nightly          | playwright E2E (against k3d) + visual snapshot                      | ❌ informational    |
| `helm.yml`                                  | chart fast                    | PR / push, paths `charts/**`   | helm lint (existing) + helm-unittest + kubeconform on rendered      | ✅                  |
| `chart-e2e.yml`                             | k3d E2E                       | push `main` + nightly          | k3d up + helm install + upgrade / rollback smoke                    | ❌ informational    |
| `images.yml`, `helpers.yml`, `gitleaks.yml` | existing                      | existing                       | existing                                                            | ✅                  |
| `trivy-cron.yml`                            | image scan                    | nightly (existing)             | trivy                                                               | ❌ informational    |
| `mutation.yml`                              | quality                       | weekly                         | mutmut on top-10 risk modules                                       | ❌ informational    |
| `flaky-tracker.yml`                         | quality                       | weekly                         | aggregates JUnit XML; opens issues > 1 % failure                    | ❌ informational    |
| `test-telemetry.yml`                        | observability                 | weekly                         | JUnit XML → SQLite → push `docs/test-telemetry/`                    | ❌ informational    |
| `dispatch.yml`                              | router                        | every PR                       | `dorny/paths-filter` → emits outputs to other workflows             | n/a                 |

**Required status checks** (branch-protected, per
`docs/conventions.md` §10.6): `lint`, `backend-fast`, `frontend-fast`,
`helm`, `images / *`, `helpers / *`, `gitleaks`. Slow tier never blocks
merge — fix-forward is the rule.

### 6.2 PR critical path

```
PR pushed:
 ├─ lint.yml ─────────────────────────────── 30 s
 ├─ backend-fast.yml ─────────────────────── 3 min   ◀── critical path
 ├─ frontend-fast.yml ────────────────────── 2 min
 ├─ helm.yml ────────────────────────────── 1 min
 ├─ images.yml (build only) ──────────────── 3 min
 ├─ helpers.yml ─────────────────────────── 3 min
 └─ gitleaks.yml ─────────────────────────── 20 s

Parallel → wall clock ≈ 3 min.
```

### 6.3 Selective testing via path-aware triggers

`dispatch.yml` uses `dorny/paths-filter` and emits outputs that downstream
workflows gate on:

| Path changed                                    | Additional triggers beyond default                                              |
| ----------------------------------------------- | ------------------------------------------------------------------------------- |
| `backend/app/routers/jobs.py`                   | + contract/openapi (schemathesis full) + heavy/postgres                         |
| `backend/app/reconciler/jobs.py`                | + contract/openapi + heavy/postgres                                             |
| `backend/app/services/mlflow_client.py`         | + contract/mlflow + heavy/mlflow                                                |
| `backend/migrations/**`                         | + integration/migrations (up/down roundtrip) + heavy/postgres (real PG migrate) |
| `backend/app/auth/cf_access.py`                 | + integration/security (csrf, rate-limit, jwt edge)                             |
| `frontend/src/api/schema.gen.ts`                | + contract/schema_gen_drift                                                     |
| `frontend/src/components/forms/**`              | + visual snapshot                                                               |
| `frontend/src/routes/_authed.*.tsx`             | + E2E auth flow                                                                 |
| `charts/lolday/templates/networkpolicy/**`      | + helm-unittest networkpolicy + chart-e2e                                       |
| `charts/lolday/templates/kyverno/**`            | + kyverno-cli test                                                              |
| `charts/lolday/templates/monitoring/**`         | + helm-unittest monitoring + `tests/phase7/test_alert_rules_inventory.sh`       |
| `scripts/build-helpers.sh`, `scripts/deploy.sh` | + bats                                                                          |
| `.github/workflows/**`                          | + actionlint or `act` dry-run                                                   |

### 6.4 Parallelization

| Layer              | Method                                     | Tool / config                               | Gotcha                                                                                     |
| ------------------ | ------------------------------------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------ |
| pytest unit        | `-n auto`                                  | pytest-xdist                                | function-scope fixtures                                                                    |
| pytest integration | `-n auto --dist loadscope`                 | pytest-xdist                                | `loadscope` groups same-file tests on one worker, avoiding aiosqlite per-worker collisions |
| pytest contract    | **serial**                                 | n/a                                         | schemathesis runs against a single FastAPI port                                            |
| pytest heavy       | **class-level**, session-scoped containers | testcontainers session + `--dist loadgroup` | container boot is 10–30 s; never per-test                                                  |
| vitest             | default parallel                           | `--pool threads`                            | global setup must not be stateful                                                          |
| playwright         | sequential (current) → Phase 3 parallel    | `fullyParallel: true`, workers=4            | requires worker-aware AUTH_DEV_EMAIL (§R4)                                                 |
| helm-unittest      | per-suite-file parallel                    | n/a                                         | suite files independent                                                                    |
| chart-e2e (k3d)    | **serial**                                 | n/a                                         | cluster boot expensive                                                                     |

### 6.5 Anti-flaky principles (twelve rules — codified in `.claude/rules/testing.md`)

1. Tests don't touch the network unless opted-in (respx / MSW /
   testcontainers).
2. Time is injected (`freezegun`, vitest fake timers), never read.
3. Random seeds are deterministic (`hypothesis` profile, faker seed,
   vitest fake timers).
4. Test order independent (`pytest-randomly` reshuffles every run).
5. Eventually-consistent waits use `wait_for` polling, never `time.sleep`.
6. Shared resources are scope-aware; testcontainers session-scoped,
   per-test transaction rollback.
7. CI test envs block network egress by default;
   `respx assert_all_called=True`; vitest globalSetup intercepts `fetch`.
8. No mutable globals across tests; `monkeypatch` over direct patch.
9. Async / concurrency capped with `pytest-timeout=30` and
   `test.setTimeout(30_000)`.
10. Time-sensitive flows use injected clocks; never `time.sleep` for
    reconciler waits.
11. Random seeds are logged on failure so a flake is reproducible.
12. CI auto-rerun is limited: `pytest-rerunfailures --reruns=2` applies
    **only** to `@pytest.mark.flaky` tests; unmarked failures never retry.

### 6.6 Quarantine workflow

```
detect → mark (with issue link) → 14-day fix → 21-day delete
```

```python
@pytest.mark.flaky(reruns=2)
@pytest.mark.flaky_tracked(issue="https://github.com/bolin8017/lolday/issues/N")
def test_xxx(): ...
```

A pytest collection hook rejects `flaky_tracked` without an issue link.
At 14 days a Discord Spidey Warnings reminder fires; at 21 days CI blocks
until the test is fixed or deleted (the **test** is deleted, not the
source code — an unreliable test is worse than no test).

### 6.7 Test-execution telemetry

`test-telemetry.yml` (weekly cron) pulls every workflow's JUnit XML
artifact, aggregates to a small SQLite, and writes
`docs/test-telemetry/dashboard.md` with:

- Per-test 30-day P50 / P95 / P99 duration.
- Per-test 7-day failure rate.
- Flaky candidates (failure rate > 1 %).
- Slow tests (P99 > 30 s).

Minimal-effort path: `pytest --junitxml=results.xml` + `upload-artifact`

- a ~150-line aggregate script. Optional later: ship to Grafana via the
  existing monitoring stack.

### 6.8 CI minutes budget

Public-repo `ubuntu-24.04` runners are free with no minute cap, so the
slow tier may run on every `main` push and nightly. The remaining limit
is the concurrent-job cap (60 on the free public-repo plan), which lolday
never approaches.

| Scenario              | Per run     | Monthly estimate           | Cost   |
| --------------------- | ----------- | -------------------------- | ------ |
| PR fast tier          | ~13 GHA-min | 30 PR × 13 = 390 min       | $0     |
| `main` push slow tier | ~40 min     | 40 push × 40 = 1 600 min   | $0     |
| Nightly slow tier     | ~50 min     | 30 nights × 50 = 1 500 min | $0     |
| **Total**             | —           | ~3 500 min/mo              | **$0** |

## 7. Regression Matrix & Coverage Map

### 7.1 Critical feature × test-type matrix (Phase-staged)

| Feature                                   | Risk | Current               | Phase 1                                                                           | Phase 2                                                     | Phase 3                                         |
| ----------------------------------------- | ---- | --------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------- | ----------------------------------------------- |
| Job submission validation                 | H    | ✅ integration        | + contract (schemathesis); + heavy (PG concurrent)                                | + hypothesis on params                                      | —                                               |
| Job lifecycle state machine               | H    | ⚠️ partial            | + contract (`stage_end` priority); + heavy (fake-client full lifecycle); mutation | —                                                           | —                                               |
| MLflow per-user ACL                       | H    | ✅ integration        | + contract (cross-user); + heavy (real MLflow multi-user)                         | —                                                           | —                                               |
| MLflow REST API shape                     | M    | ⚠️ scattered respx    | + contract (respx replay tape)                                                    | —                                                           | —                                               |
| GPU FIFO scheduling                       | M    | ✅ integration        | + contract (prom query shape); + heavy (prom outage sim)                          | —                                                           | —                                               |
| Detector build → Harbor push              | H    | ✅ integration        | + contract (manifest schema)                                                      | + heavy (testcontainers Harbor v2)                          | —                                               |
| Migrations up/down roundtrip              | H    | ⚠️ up only            | + integration roundtrip; + heavy (real PG migrate)                                | —                                                           | —                                               |
| Helm chart NP / PSS / Kyverno             | H    | ❌ none               | + helm-unittest; + kubeconform; + kyverno-cli test                                | + chart-e2e (k3d enforce)                                   | —                                               |
| Helm chart monitoring (alert rules)       | M    | ⚠️ phase7 shell       | + helm-unittest alertrules; + amtool check                                        | —                                                           | —                                               |
| Auth JWT shape                            | M    | ✅ unit               | —                                                                                 | + contract (schemathesis 401/403); + heavy (JWKS reflector) | —                                               |
| Frontend RJSF v6 forms                    | H    | ⚠️ unit partial       | —                                                                                 | + integration (vitest + MSW flow); + visual snapshot        | + E2E full path                                 |
| Frontend i18n drift                       | M    | ❌ none               | —                                                                                 | —                                                           | + contract (missing-key); + visual cross-locale |
| Frontend role-based UI                    | H    | ❌ E2E single persona | —                                                                                 | + integration (MSW per role)                                | + E2E multi-persona                             |
| Discord notify embed shape                | L    | ⚠️ unit               | + contract                                                                        | —                                                           | —                                               |
| Scripts (`deploy.sh`, `build-helpers.sh`) | M    | ❌ none               | + bats                                                                            | + chart-e2e drives deploy                                   | —                                               |

Legend: ✅ existing solid · ⚠️ partial · ❌ none · 🆕 to be added.

### 7.2 Risk-class reduction per new test type

Classes: 0. **meta-quality** — does the test really assert correct behaviour?

1. **silent breakage** — merge passes; user discovers later.
2. **security regression** — defence weakens silently.
3. **prod-only bug** — passes in dev / CI; fails in prod.
4. **known-bug regression** — fixed once; recurs after a refactor.
5. **infra regression** — deploy / migration / rollback.

| New test type                       | Concrete regression it catches                                              | Class |
| ----------------------------------- | --------------------------------------------------------------------------- | ----- |
| schemathesis                        | OpenAPI ↔ actual drift (e.g., PR #156 RJSF v6 schema enum)                  | 1     |
| helm-unittest                       | #152 / #150 / #179 / #181 chain — none of which have a chart test today     | 1     |
| kubeconform                         | rendered manifest with removed API version                                  | 1     |
| kyverno-cli test                    | Kyverno policy reverts to non-enforce (P4 H-23 risk)                        | 2     |
| testcontainers Postgres             | aiosqlite ≠ PG (JSONB query, FK cascade, advisory lock, deadlock detection) | 3     |
| testcontainers real MLflow          | MLflow REST API contract drift on version bump                              | 3     |
| testcontainers MinIO + S3 streaming | streaming download memory leak (P6 M-mlflow-stream regression guard)        | 4     |
| k3d chart-e2e                       | helm upgrade carry-over values; PVC subPath corruption                      | 5     |
| playwright multi-persona            | role-based UI silent break                                                  | 2     |
| visual snapshot                     | RJSF v6 layout regression                                                   | 1     |
| MSW frontend integration            | new endpoint added without test                                             | 1     |
| schema_gen_drift                    | handstitched fields silently regen-reverted (tech debt #14)                 | 4     |
| migrations up/down roundtrip        | Alembic downgrade never tested                                              | 5     |
| hypothesis (state machine)          | new `JobStatus` value missing from `_RESOURCE_PROFILE_GPU_COUNT`            | 1     |
| mutation (observation)              | tests that run but don't assert correctness                                 | 0     |
| bats (scripts)                      | deploy.sh / build-helpers.sh edge cases (PR #184 Python heredoc)            | 1     |

### 7.3 High-risk module coverage map (representative entries)

**`backend/app/routers/jobs.py` (920 LOC, risk rank 1)**

| Layer       | File                                                            | Scenarios                                                              |
| ----------- | --------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Unit        | `tests/unit/routers/test_jobs_payload_normalizer.py`            | payload renaming, default fill                                         |
| Integration | `tests/integration/routers/test_jobs.py` (existing, relocated)  | happy path × 3 stages; invalid param 422; quota 429                    |
| Contract    | `tests/contract/openapi/test_schemathesis_jobs_endpoints.py` 🆕 | fuzzed payload (int overflow, unicode, null fields)                    |
| Heavy       | `tests/heavy/postgres/test_jobs_concurrent_submit.py` 🆕        | two users submit same detector simultaneously; FIFO position race-free |
| Hypothesis  | `tests/unit/invariants/test_job_payload_invariant.py` 🆕        | any `train_params` dict normalises or fails explicitly                 |
| Mutation    | target mutmut killed ≥ 80 %                                     | observation                                                            |

**`backend/app/reconciler/jobs.py` (496 LOC, risk rank 2)**

| Layer       | File                                                           | Scenarios                                                 |
| ----------- | -------------------------------------------------------------- | --------------------------------------------------------- |
| Unit        | `tests/unit/reconciler/test_jobs_status_transitions.py` 🆕     | all six legal edges of the JobStatus machine              |
| Integration | `tests/integration/reconciler/test_jobs.py` (existing reorg)   | reconcile loop scan; deletion handling                    |
| Contract    | `tests/contract/openapi/test_stage_end_event_priority.py` 🆕   | Phase 11b contract: `stage_end` event beats Volcano phase |
| Heavy       | `tests/heavy/k8s_fake/test_volcano_vcjob_full_lifecycle.py` 🆕 | Pending → Running → Completed; DB Job row stays in sync   |
| Hypothesis  | `tests/unit/invariants/test_job_status_state_machine.py` 🆕    | every illegal transition raises (no silent demotion)      |

**`charts/lolday/templates/networkpolicy/` (new test area)**

| Layer         | File                                             | Scenarios                                                                                    |
| ------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Static        | `lint.yml` via kubeconform                       | NetworkPolicy v1 schema valid                                                                |
| helm-unittest | `charts/lolday/tests/networkpolicy_test.yaml` 🆕 | egress allows traefik; ingress allows cloudflared; `feedback_traefik_is_np_source` invariant |
| chart-e2e     | `tests/e2e_chart/np_enforce.sh` 🆕               | k3d cluster + helm install + cross-ns traffic blocked                                        |

Full coverage map for all ten modules ships with Phase 1 (`docs/test-coverage-map.md`).

### 7.4 Coverage targets

- Backend `pytest --cov=app` ≥ **85 %** (Codecov gate; from current
  unmeasured).
- Backend contract: 100 % public endpoints via schemathesis (auto-derived
  from `/openapi.json`).
- Backend heavy: top-10 risk modules each have at least one heavy-tier
  scenario (scenario coverage, not line coverage).
- Frontend vitest coverage scope extended to `src/components/` +
  `src/routes/`; target ≥ **70 %**.
- Frontend E2E: ≥ 15 critical flows (Phase 3 exit).
- helm-unittest: every `templates/<subdir>/` has ≥ 1 suite; key resources
  (backend Deployment, NP, Kyverno policy, AlertmanagerConfig) have ≥ 5
  cases.
- Mutation testing: top-10 modules ≥ **80 %** mutmut killed (Phase 4
  exit).

## 8. Anti-Flaky, Parallelization, Telemetry Details

See §6.5 – §6.8 for the twelve rules, quarantine flow, parallelization
matrix, and telemetry pipeline. Concrete Phase-1 ship items:

1. `backend/pyproject.toml`:

   ```toml
   [tool.pytest.ini_options]
   asyncio_default_fixture_loop_scope = "function"
   addopts = "-n auto --dist loadscope --maxfail=10 --durations=20 --strict-markers --timeout=30"
   markers = [
       "heavy: testcontainers slow tier",
       "contract: API or manifest contract test",
       "flaky_tracked: known flaky test with linked GitHub issue",
   ]
   ```

2. Backend dev dependencies (add to `backend/pyproject.toml`):
   `pytest-xdist`, `pytest-timeout`, `pytest-randomly`,
   `pytest-rerunfailures`, `pytest-split`, `freezegun`, `hypothesis`,
   `schemathesis`, `testcontainers[postgres,minio]`, `mlflow`,
   `kubernetes-fake-client`, `polyfactory`, `pytest-testmon` (dev-only).

3. `frontend/vitest.config.ts`: `testTimeout: 10_000`;
   `coverage.thresholds: { lines: 70 }`; `coverage.include` extended to
   `src/components/**` and `src/routes/**`.

4. `frontend/playwright.config.ts`: `retries: 0` fast tier; `retries: 1`
   for `@flaky` annotation; `fullyParallel` stays false until §R4 lands.

5. `.claude/rules/testing.md` (new): the twelve anti-flaky rules, the
   parallelization matrix, the quarantine workflow.

6. `charts/lolday/tests/helm-unittest-runner.sh` (new): local runner;
   `helm.yml` invokes it in CI.

## 9. Refactoring Proposals

Each proposal is gated by the question "is this needed to land the
corresponding test type?". Project `CLAUDE.md` permits breaking changes;
none of these requires a backwards-compat path.

### R1 — Split the 850-line `backend/tests/conftest.py` (Phase 1, 3–5 d)

**Test-hostile state**

- 850 lines, 30 commits in 30 days; autouse fixtures affect every test
  silently.
- The 400-line `mock_mlflow` autouse stub prevents heavy-tier real-MLflow
  use without disable boilerplate.

**Proposal** — split per §5.3 into four conftests by layer, plus a
`factories/` directory using `polyfactory`.

**Unlocks** — clean heavy tier (no autouse pollution); fast contract tier;
maintainability (factory edits no longer touch conftest).

**Risk** — low. Existing 99 % of tests change only their import path.

### R2 — `MlflowClient` via FastAPI lifespan injection (Phase 1, 3 d)

**Test-hostile state**

```python
# services/mlflow_client.py — current
_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()

async def _request(method, url, ...):
    global _client
    async with _lock:
        if _client is None:
            _client = httpx.AsyncClient(...)
    return await _client.request(...)
```

Module-level lazy singleton with lock; tests can only intercept via
`monkeypatch._client` or autouse stub.

**Proposal** — move to a `MlflowClient` class, instantiated in
`app/main.py`'s `lifespan`, retrieved via `Depends(get_mlflow)`. Tests
use `app.dependency_overrides[get_mlflow] = lambda: FakeMlflowClient()`.

**Unlocks** — heavy tier can point at real MLflow via
`testcontainers.mlflow`; no global monkeypatch needed.

**Risk** — medium. Touches every caller; land in two PRs (add new
Depends; migrate callers).

### R3 — `routers/jobs.py` service extraction (Phase 2, 1–2 w)

**Test-hostile state** — 920 LOC; 21 intra-app imports; HTTP +
validation + DB + MLflow + K8s + Discord + rate-limit all live in
router.

**Proposal**

```
backend/app/routers/jobs.py            # < 250 lines: HTTP adapter only
backend/app/services/job_submission.py # pure: submit_job(session, user, payload) -> Job
backend/app/services/job_validation.py # pure: validate_submission(payload, detector, …) -> ValidatedPayload
backend/app/services/job_dispatch.py   # pure: dispatch_to_volcano(job, k8s_client) -> None
```

**Unlocks** — unit-test pure business logic without `TestClient` (5–10×
faster); cleaner schemathesis surface; hypothesis-friendly
`validate_submission` signature.

**Risk** — medium. Endpoint-by-endpoint migration; old inline logic
coexists during transition.

### R4 — `AUTH_DEV_MODE` multi-persona (Phase 2, 3–5 d)

**Test-hostile state** — `AUTH_DEV_EMAIL` hardcodes a single user; E2E
cannot exercise role-based UI; Phase 3 multi-persona Playwright parallel
is blocked.

**Proposal**

```python
AUTH_DEV_PERSONAS: dict[str, dict] = {
    "admin": {"email": "admin@dev.local", "role": "admin"},
    "developer": {"email": "dev@dev.local", "role": "developer"},
    "user": {"email": "user@dev.local", "role": "user"},
}
```

Frontend E2E helper: `loginAs(page, role)` sets
`X-Dev-Persona: <role>`.

**Unlocks** — multi-persona E2E; worker-aware Playwright parallel
(`AUTH_DEV_EMAIL=worker-${WORKER_INDEX}@example.com`); per-role
integration tests via MSW.

**Risk** — low. Dev-mode only; production unaffected.

### R5 — `schema.gen.ts` handstitched-field hard fix (Phase 3, 3–5 d)

**Test-hostile state** (`docs/architecture.md` §10 tech debt #14) — the
two handstitched fields (`detector_defaults` on `JobRead`; `gpu1` in
`ResourceProfile` enum) get silently overwritten on `pnpm gen-api-types`.

**Proposal**

```
frontend/src/api/schema.gen.ts          # 100 % codegen; never hand-edited
frontend/src/api/schema.handstitched.ts # NEW: only the two extensions
frontend/src/api/schema.ts              # NEW: merges and re-exports
```

Plus a contract test that asserts the two fields appear in the live
`/openapi.json` (so the moment the backend ships them, the
`schema.handstitched.ts` can be deleted).

**Unlocks** — closes tech debt #14 at the root, not a workaround.

**Risk** — low. Two fields; TypeScript strict mode catches stragglers.

### R6 — Scripts shell → Python lib + bats (ongoing, Phase 4+)

**Test-hostile state** — `deploy.sh`, `build-helpers.sh`,
`recover-harbor.sh` mix bash with embedded Python heredocs; recent fixes
(#184 Python heredoc, #185 cosign-sign, #155 apostrophe escape) all
shipped without any test gate.

**Proposal** — incremental. Whenever a script is touched, extract the
non-trivial logic into a Python module under `scripts/lib/` and add
pytest unit + `bats` smoke. The bash file becomes orchestration.

```
scripts/lib/harbor_api.py
scripts/lib/helpers_lock.py
scripts/lib/k3s_safety.py
scripts/lib/deploy_helpers.py

scripts/tests/lib/test_harbor_api.py     # pytest unit
scripts/tests/bats/test_deploy_smoke.bats
```

**Unlocks** — `bats-core` becomes a first-class test layer.

**Risk** — low. Strictly incremental; no big-bang rewrite.

### Explicitly rejected refactors

| Proposed elsewhere                                         | Why we are not doing it                                                                                               |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Hexagonal `reconciler/jobs.py` (port/adapter pattern)      | Over-engineered for lolday's scale; R2's lifespan injection delivers the same testability without the abstraction tax |
| `services/k8s.py` wrapped in a `KubernetesClient` Protocol | `kubernetes-fake-client` lib already supplies CRD + error simulation                                                  |
| `services/build.py` plan/execute split                     | 417 LOC is manageable; testcontainers BuildKit covers the heavy-tier need                                             |
| Reconciler watch pattern replacing 10-s polling            | Single-node K3s; polling latency is acceptable; watch adds significant complexity                                     |
| Monorepo tooling (nx, turborepo)                           | backend and frontend already separate (uv + pnpm); no pain point to solve                                             |

## 10. Roadmap

### Phase 0 — Doc & infra unlock (< 1 week)

| #    | Deliverable                                                                                                                                                                  |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D0.1 | Update `docs/conventions.md` §10.6 footnote (repo is now public)                                                                                                             |
| D0.2 | Enable branch-protection rules: required status checks = `lint`, `backend-fast`, `frontend-fast`, `helm`, `images/*`, `helpers/*`, `gitleaks`; linear history; no force-push |
| D0.3 | Enable Codecov coverage threshold gate (backend ≥ 80 %, frontend ≥ 65 % to start; raised in Phase 1)                                                                         |
| D0.4 | Create `.claude/rules/testing.md` with the twelve anti-flaky rules and quarantine workflow                                                                                   |
| D0.5 | GitHub labels: `flaky`, `test-coverage-gap`, `tech-debt-tests`                                                                                                               |

> **Status note (2026-05-15)** — D0.1 is already complete in commit
> `b8e2998` (`docs/conventions.md` §10.6 footnote updated for the public
> flip). The non-status-check parts of D0.2 (PR required, force-push
> blocked, delete blocked, linear history) are already enforced. The
> remaining D0.2 work is configuring `required_status_checks` for the
> seven gates listed in §6.1; the current §10.6 step-2 list uses the
> pre-redesign workflow names and must be updated once Phase 1 renames
> `backend.yml` → `backend-fast.yml` and reactivates `frontend-fast.yml`.

**Effort**: 3–5 d. **Risk reduction**: procedural (merge discipline moves
from social to API-enforced).

### Phase 1 — Foundation, critical path, Helm baseline (~3–4 weeks)

| #     | Deliverable                                                                                                                                                                                                                           |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1.1  | Test directory reorg per §5.1 — move 96 backend tests into subdirs                                                                                                                                                                    |
| D1.2  | R1: split `conftest.py` into four files                                                                                                                                                                                               |
| D1.3  | Dev-deps: pytest-xdist / -timeout / -randomly / -rerunfailures / -split, freezegun, hypothesis, schemathesis, testcontainers[postgres,minio], mlflow, kubernetes-fake-client, polyfactory                                             |
| D1.4  | `pyproject.toml` addopts + markers (§8)                                                                                                                                                                                               |
| D1.5  | R2: `MlflowClient` lifespan injection                                                                                                                                                                                                 |
| D1.6  | Split `backend.yml` → `backend-fast.yml` + `backend-slow.yml`                                                                                                                                                                         |
| D1.7  | Backend contract tier first batch: schemathesis jobs / detectors / users_me; respx MLflow replay tape; kubeconform on rendered vcjob                                                                                                  |
| D1.8  | Backend heavy tier first batch: `heavy/postgres/test_jobs_concurrent_submit.py`; `heavy/postgres/test_migrations_real_pg.py` (up/down); `heavy/mlflow/test_real_mlflow_lifecycle.py`; `heavy/k8s_fake/test_volcano_full_lifecycle.py` |
| D1.9  | helm-unittest plugin in CI; first six suites: backend_deployment, networkpolicy, kyverno_policy, monitoring_alertrules, alertmanagerconfig, pss; `values-test.yaml`                                                                   |
| D1.10 | kubeconform + kyverno-cli in `lint.yml` (pre-commit and CI)                                                                                                                                                                           |
| D1.11 | New `chart-e2e.yml`: k3d ephemeral + `helm install` + `curl /healthz` + helm upgrade/rollback smoke                                                                                                                                   |
| D1.12 | New `dispatch.yml` using `dorny/paths-filter`                                                                                                                                                                                         |
| D1.13 | New `flaky-tracker.yml` weekly cron                                                                                                                                                                                                   |
| D1.14 | Hypothesis-driven invariant tests on `models/job.py::JobStatus` and `ResourceProfile` enum totality                                                                                                                                   |

**Effort**: 3–4 weeks part-time (~2 weeks full-time).

**Risk reduction**

| Class                  | From | To   |
| ---------------------- | ---- | ---- |
| Cat 1 silent breakage  | 30 % | 60 % |
| Cat 3 prod-only bug    | 10 % | 70 % |
| Cat 5 infra regression | 0 %  | 60 % |

**Exit criteria** — all existing 96 tests green after reorg;
`backend-fast.yml` < 4 min; `backend-slow.yml` < 15 min; `chart-e2e.yml`
deploy smoke green; six helm-unittest suites green.

### Phase 2 — Security boundaries + frontend integration (~3–4 weeks)

| #     | Deliverable                                                                                                                                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D2.1  | R3: routers/jobs.py service extraction                                                                                                                                                      |
| D2.2  | R4: AUTH_DEV_MODE multi-persona                                                                                                                                                             |
| D2.3  | Security contract tests: cross-user MLflow ACL (schemathesis + heavy real MLflow multi-user); CSRF token rotation full flow; rate-limit per-user vs per-IP; audit log durability on real PG |
| D2.4  | Auth contract tests: JWT shape via JWKS reflector (testcontainers); JWKS cache TTL with freezegun                                                                                           |
| D2.5  | Kyverno + PSS enforce E2E: `tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh`; `test_pss_enforce_privileged.sh`                                                                      |
| D2.6  | Frontend MSW + integration: `mocks/{handlers,server}.ts`; `integration/routes/{jobs,detectors}.test.tsx`; `integration/forms/JobSubmitForm.flow.test.tsx`                                   |
| D2.7  | Frontend visual regression: `visual/{rjsf_form,sidebar,page_header}_snapshots.spec.ts`                                                                                                      |
| D2.8  | Frontend contract: `contract/schema_gen_drift.test.ts` against backend `/openapi.json`                                                                                                      |
| D2.9  | Reactivate `frontend-slow.yml`: playwright E2E against k3d                                                                                                                                  |
| D2.10 | Extend vitest coverage to `src/components/` + `src/routes/`; threshold 70 %; raise Codecov gate                                                                                             |

**Effort**: 3–4 weeks.

**Risk reduction**

| Class                      | From | To   |
| -------------------------- | ---- | ---- |
| Cat 1 silent breakage      | 60 % | 75 % |
| Cat 2 security regression  | 80 % | 95 % |
| Cat 3 prod-only bug        | 70 % | 75 % |
| Cat 4 known-bug regression | 30 % | 60 % |

**Exit criteria** — every P1–P6 finding has a test gate (current partial
→ full); `frontend-fast.yml` < 3 min; `frontend-slow.yml` < 25 min;
vitest coverage `src/components/` + `src/routes/` ≥ 70 %.

### Phase 3 — Frontend full E2E + role-based + i18n (~2–3 weeks)

| #    | Deliverable                                                                                                                                                      |
| ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D3.1 | Page object models: `helpers/{job-submit,detector,model,run-detail}.po.ts`                                                                                       |
| D3.2 | Multi-persona E2E: `e2e/auth/{role-based-visibility,admin-only-actions}.spec.ts`                                                                                 |
| D3.3 | Critical user flow E2E: `e2e/jobs/full-lifecycle.spec.ts`; `e2e/detectors/build-and-list.spec.ts`; `e2e/models/transfer-and-delete.spec.ts`                      |
| D3.4 | Playwright `fullyParallel: true` workers=4 (using worker-aware persona)                                                                                          |
| D3.5 | i18n drift contract: `contract/i18n_missing_key.test.ts` (zh-TW ⊇ en); `visual/i18n_cross_locale.spec.ts`                                                        |
| D3.6 | a11y baseline via `@axe-core/playwright` on critical pages                                                                                                       |
| D3.7 | Mobile E2E expansion (current 5 → 8+ specs)                                                                                                                      |
| D3.8 | R5: split `schema.gen.ts` into 100 %-codegen file + `schema.handstitched.ts` (the two extensions) + merged `schema.ts`; closes architecture.md §10 tech debt #14 |

**Effort**: 2–3 weeks.

**Risk reduction**

| Class                      | From | To   |
| -------------------------- | ---- | ---- |
| Cat 1 silent breakage      | 75 % | 85 % |
| Cat 4 known-bug regression | 60 % | 65 % |
| Cat 5 infra regression     | 65 % | 70 % |

**Exit criteria** — playwright parallel green; critical-flow E2E ≥ 15
specs; a11y baseline runnable.

### Phase 4 — Scripts + mutation + test telemetry (~2 weeks)

| #    | Deliverable                                                                                                                  |
| ---- | ---------------------------------------------------------------------------------------------------------------------------- |
| D4.1 | `bats` GHA action + `tests/bats/`                                                                                            |
| D4.2 | R6 incremental kick-off: extract `scripts/lib/{harbor_api,helpers_lock}.py`; add pytest unit + bats smoke                    |
| D4.3 | New `mutation.yml` weekly cron: mutmut on top-10 risk modules; results pushed to `docs/test-telemetry/mutation-<date>.md`    |
| D4.4 | New `test-telemetry.yml` weekly cron: JUnit XML aggregate → SQLite → push `docs/test-telemetry/`; summary to Spidey Warnings |
| D4.5 | `.claude/rules/scripts-and-ops.md` adds "touched script must add lib + test" rule                                            |
| D4.6 | `docs/test-telemetry/dashboard.md`: flaky list, slow tests, coverage trend                                                   |

**Effort**: 2 weeks.

**Risk reduction**

| Class                  | From | To   |
| ---------------------- | ---- | ---- |
| Cat 0 meta-quality     | 0 %  | 60 % |
| Cat 1 silent breakage  | 85 % | 90 % |
| Cat 5 infra regression | 70 % | 75 % |

**Exit criteria** — ≥ 2 scripts have extracted libs and tests; mutation
mutmut killed ≥ 60 % on top-10; telemetry dashboard populated.

### Phase 5 — Optional advanced (only on demonstrated need)

| #    | Topic                                            | Trigger condition           |
| ---- | ------------------------------------------------ | --------------------------- |
| D5.1 | Chaos (toxiproxy on PG; stress-ng on reconciler) | Production chaos incident   |
| D5.2 | Performance (k6 submit burst, MLflow streaming)  | Perf incident or 5× traffic |
| D5.3 | 24-hour leak detection                           | Suspected reconciler leak   |
| D5.4 | Fuzzing (AFL / boofuzz) on manifest parser       | Security-research need      |
| D5.5 | Stateful property testing on reconciler          | Mutation score stuck < 65 % |

**Effort**: 2–4 weeks per item; not started unless triggered.

### Cumulative risk-class coverage

| Phase complete     | Cat 0 meta | Cat 1 silent | Cat 2 security | Cat 3 prod | Cat 4 known | Cat 5 infra |
| ------------------ | ---------- | ------------ | -------------- | ---------- | ----------- | ----------- |
| Current            | 0 %        | 30 %         | 75 %           | 10 %       | 20 %        | 0 %         |
| Phase 0            | 0 %        | 30 %         | 75 %           | 10 %       | 20 %        | 0 %         |
| **Phase 1**        | 0 %        | 60 %         | 80 %           | 70 %       | 30 %        | 60 %        |
| **Phase 2**        | 0 %        | 75 %         | 95 %           | 75 %       | 60 %        | 65 %        |
| **Phase 3**        | 0 %        | 85 %         | 95 %           | 75 %       | 65 %        | 70 %        |
| **Phase 4**        | 60 %       | 90 %         | 95 %           | 80 %       | 70 %        | 75 %        |
| Phase 5 (optional) | 75 %       | 95 %         | 95 %           | 90 %       | 80 %        | 90 %        |

Percentages are scenario-level coverage of the named risk class, not line
coverage.

### Cumulative effort

| Phase        | Effort (part-time) | Cumulative |
| ------------ | ------------------ | ---------- |
| 0            | 3–5 d              | < 1 week   |
| 1            | 3–4 weeks          | ~5 weeks   |
| 2            | 3–4 weeks          | ~9 weeks   |
| 3            | 2–3 weeks          | ~12 weeks  |
| 4            | 2 weeks            | ~14 weeks  |
| 5 (optional) | 2–4 weeks per item | —          |

Core (Phase 0–4) lands in ~3 months part-time; full-time compresses to
about seven weeks.

## 11. Long-term Maintenance

### 11.1 Per-PR discipline (codified in `.claude/rules/testing.md` and area rules)

- New router / service / reconciler → unit + integration + contract.
- New alert rule / NetworkPolicy / Kyverno policy → helm-unittest case.
- New frontend component / route → vitest unit + i18n key + visual
  snapshot if form.
- Bug fix → regression test whose name carries the GitHub issue ID
  (e.g. `test_jwt_invalid_token_error_h27`).
- Touched script → extracted lib + test.

### 11.2 Quarterly health check (Claude can schedule)

- Flaky-test count vs the 1 % SLO.
- Coverage trend for backend / frontend / chart.
- Mutation score trend.
- Slow-test top-20.
- Contract drift incidents.

### 11.3 Test code ownership

- `.claude/rules/testing.md` is the umbrella rule.
- Per-area rules (backend, frontend, charts, scripts) carry a "Tests"
  section.
- A new test type or framework needs a brainstorm → spec → plan flow
  before merge (`docs/superpowers/specs/<date>-<topic>-test-design.md`).

### 11.4 Framework upgrade SOP

- Quarterly audit of hypothesis, schemathesis, testcontainers,
  helm-unittest, playwright, k3d major versions.
- Dependabot PRs the bumps; after merge, mutation + telemetry verify no
  regression.

### 11.5 Failure escalation

- PR fast-tier red → blocked at the branch-protection layer (after Phase
  0).
- Slow-tier red → `flaky-tracker.yml` auto-opens an issue assigned to
  the PR author; 24-hour follow-up SLO.
- Flaky-tracked test → 14-day fix SLO; 21-day delete.

### 11.6 Quarterly retrospective

- Review the quarter's postmortems; classify whether a test caught the
  bug or not.
- Missed → add test type or refactor module.
- Always-green-never-caught-anything → downsample or delete.

## 12. References

### Specs that informed this design

- `docs/superpowers/specs/2026-04-30-github-actions-cicd-design.md` —
  current GHA architecture; this spec extends it.
- `docs/superpowers/specs/2026-05-12-security-hardening-design.md` — the
  88 findings whose tests we now lock in place.
- `docs/superpowers/specs/2026-05-11-mlflow-data-model-redesign-design.md`
  — MLflow API surface that schemathesis + testcontainers must cover.
- `docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md` —
  GPU signal contract.
- `docs/superpowers/specs/2026-05-05-gpu-fifo-anti-starvation-design.md`
  — FIFO scheduler invariants.

### Postmortems

- `docs/postmortems/2026-03-31-cilium-ssh-incident.md` — operational
  discipline reference.
- (Implicit) 2026-04-21 Prometheus WAL corruption — motivates chart-e2e
  PVC subPath coverage.

### Project rules

- `~/.claude/CLAUDE.md` §Mainstream practices first, §Root-cause first.
- Project root `CLAUDE.md` — hard rules (SSH safety, no China-origin
  software, lint discipline, deploy-not-development platform).
- `.claude/rules/github-actions.md` — pinning, permissions,
  pull_request_target ban.
- `docs/conventions.md` §10 — workflow inventory, image tag rules,
  branch protection.

### Tool documentation

- schemathesis https://schemathesis.readthedocs.io/
- testcontainers-python https://testcontainers-python.readthedocs.io/
- helm-unittest https://github.com/helm-unittest/helm-unittest
- kubeconform https://github.com/yannh/kubeconform
- kyverno-cli https://kyverno.io/docs/kyverno-cli/
- k3d https://k3d.io
- MSW https://mswjs.io/
- hypothesis https://hypothesis.readthedocs.io/
- bats-core https://bats-core.readthedocs.io/

## Appendix A — Tool pinning

When Phase 1 lands, the following pins go into `backend/pyproject.toml`
and `frontend/package.json`. Dependabot manages bumps weekly per
`.github/dependabot.yml`.

| Tool                  | Initial pin (May 2026) |
| --------------------- | ---------------------- |
| schemathesis          | ^3.36                  |
| testcontainers-python | ^4.7                   |
| hypothesis            | ^6.114                 |
| polyfactory           | ^2.18                  |
| pytest-xdist          | ^3.6                   |
| pytest-timeout        | ^2.3                   |
| pytest-randomly       | ^3.15                  |
| pytest-rerunfailures  | ^14.0                  |
| pytest-split          | ^0.9                   |
| freezegun             | ^1.5                   |
| helm-unittest         | ^0.5                   |
| kubeconform           | ^0.6                   |
| kyverno-cli           | ^1.13                  |
| k3d (CI)              | ^5.7                   |
| MSW                   | ^2.4                   |
| @axe-core/playwright  | ^4.10                  |
| bats-core             | ^1.11                  |
