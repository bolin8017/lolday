# Test Architecture Phase 1 — Foundation, Critical Path, Helm Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Phase 1 of the test architecture redesign — reorg backend tests into a layered directory, split the 850-line conftest, refactor `MlflowClient` onto FastAPI lifespan injection, add contract + heavy tiers, six helm-unittest suites, and the new `backend-fast` / `backend-slow` / `chart-e2e` / `dispatch` / `flaky-tracker` workflows.

**Architecture:** Two-tier CI (PR fast tier ≤ 4 min + `main`/nightly slow tier ≤ 15 min). Backend pytest gains `hypothesis` (state-machine invariants), `schemathesis` (API contract), `testcontainers-python` (real Postgres / MLflow / MinIO), and `kubernetes-fake-client` (Volcano CRD). Helm chart gains `helm-unittest`, `kubeconform`, and `kyverno-cli`. New workflows split fast vs slow and route triggers via `dorny/paths-filter`.

**Tech Stack:** pytest, pytest-xdist / -timeout / -randomly / -rerunfailures / -split, hypothesis, schemathesis, testcontainers-python (postgres, minio), mlflow, kubernetes-fake-client, polyfactory, freezegun, helm-unittest, kubeconform, kyverno-cli, k3d, GitHub Actions.

---

## Reference

**Source spec:** `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`

This plan implements Phase 1 (§10 of the spec). Phase 0 D0.1 (conventions footnote) is already complete in commit `b8e2998`; non-status-check parts of D0.2 (branch protection rules) are already enforced. The remaining Phase 0 items (D0.2 required-status-checks list, D0.3 Codecov gate, D0.5 GitHub labels) are listed below as Prerequisites. Phase 0 D0.4 (`.claude/rules/testing.md`) is Task 1 of this plan because every subsequent task references its markers and conventions.

Phases 2 / 3 / 4 / 5 each get their own plans, written after Phase 1 ships and is reviewed.

## Prerequisites (small Phase-0 PRs that may land in parallel with this plan)

- [ ] **D0.2 — required-status-checks list.** Configure via `gh api`:

  ```bash
  gh api -X PUT repos/bolin8017/lolday/branches/main/protection/required_status_checks \
    -F strict=true \
    -F 'contexts[]=lint / pre-commit' \
    -F 'contexts[]=backend / pytest' \
    -F 'contexts[]=frontend / unit' \
    -F 'contexts[]=helm / lint-template' \
    -F 'contexts[]=images / build-image (backend)' \
    -F 'contexts[]=images / build-image (frontend)' \
    -F 'contexts[]=helpers / build-helper (build-helper)' \
    -F 'contexts[]=helpers / build-helper (job-helper)'
  ```

  After this plan ships Task 33–35, re-run the same command with the new names (`backend-fast / pytest`, etc.).

- [ ] **D0.3 — Codecov gate.** Create `.codecov.yml` at repo root:

  ```yaml
  coverage:
    status:
      project:
        default:
          target: 80%
          threshold: 1%
      patch:
        default:
          target: 80%
  comment:
    layout: "diff, files"
  ```

- [ ] **D0.5 — Three GitHub labels:**

  ```bash
  gh label create flaky --color D93F0B \
    --description "Test marked with @pytest.mark.flaky; quarantine SLO 14d/21d"
  gh label create test-coverage-gap --color FBCA04 \
    --description "Critical path lacks test coverage"
  gh label create tech-debt-tests --color BFDADC \
    --description "Test-related tech debt in architecture.md §10"
  ```

## File Structure

**New files**

- `.claude/rules/testing.md` (D0.4)
- `backend/tests/{unit,integration,contract,heavy,factories}/__init__.py`
- `backend/tests/integration/conftest.py`
- `backend/tests/heavy/conftest.py`
- `backend/tests/contract/conftest.py`
- `backend/tests/factories/{job,user,detector,dataset}_factory.py`
- `backend/tests/fixtures/mlflow/recorded/<*>.json` (respx tapes)
- `backend/tests/contract/openapi/test_schemathesis_jobs.py`
- `backend/tests/contract/openapi/test_schemathesis_detectors.py`
- `backend/tests/contract/openapi/test_schemathesis_users_me.py`
- `backend/tests/contract/mlflow/test_mlflow_response_shape.py`
- `backend/tests/contract/volcano/test_vcjob_manifest_kubeconform.py`
- `backend/tests/heavy/postgres/test_jobs_concurrent_submit.py`
- `backend/tests/heavy/postgres/test_migrations_real_pg.py`
- `backend/tests/heavy/mlflow/test_real_mlflow_lifecycle.py`
- `backend/tests/heavy/k8s_fake/test_volcano_full_lifecycle.py`
- `backend/tests/unit/invariants/test_job_status_state_machine.py`
- `backend/tests/unit/invariants/test_resource_profile_enum_totality.py`
- `charts/lolday/values-test.yaml`
- `charts/lolday/tests/backend_deployment_test.yaml`
- `charts/lolday/tests/networkpolicy_test.yaml`
- `charts/lolday/tests/kyverno_policy_test.yaml`
- `charts/lolday/tests/monitoring_alertrules_test.yaml`
- `charts/lolday/tests/alertmanagerconfig_test.yaml`
- `charts/lolday/tests/pss_test.yaml`
- `.github/workflows/backend-fast.yml`
- `.github/workflows/backend-slow.yml`
- `.github/workflows/chart-e2e.yml`
- `.github/workflows/dispatch.yml`
- `.github/workflows/flaky-tracker.yml`

**Modified files**

- `backend/pyproject.toml` — dev deps + `[tool.pytest.ini_options]` addopts + markers
- `backend/app/main.py` — `lifespan` instantiates `MlflowClient`
- `backend/app/services/mlflow_client.py` — class-based; module-level singleton removed
- `backend/app/deps.py` — adds `get_mlflow` dependency
- `backend/app/routers/jobs.py`, `routers/experiments_proxy.py`, `reconciler/jobs.py` — migrate to `Depends(get_mlflow)`
- `backend/tests/conftest.py` — slimmed to < 200 lines
- `backend/tests/<all existing test files>` — moved into `tests/integration/<subarea>/`
- `.github/workflows/lint.yml` — adds `kubeconform` + `kyverno-cli validate`
- `.github/workflows/helm.yml` — adds `helm-unittest` step

**Deleted files**

- `.github/workflows/backend.yml` (replaced by `backend-fast.yml` + `backend-slow.yml`)

---

## Tasks

### Task 1: Create `.claude/rules/testing.md` (D0.4 — prerequisite for every other task)

**Files:**

- Create: `.claude/rules/testing.md`

- [ ] **Step 1: Create the rule file**

```markdown
# Testing rules

Path scope: anything under `backend/tests/`, `frontend/tests/`,
`charts/lolday/tests/`, `tests/`, plus all `*.test.tsx`, `*.spec.ts`,
`test_*.py`, `*_test.yaml`.

Source spec:
`docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`.

## Twelve anti-flaky rules

1. **No network in tests.** backend → `respx` with `assert_all_called=True`;
   frontend → MSW (`tests/mocks/handlers.ts`) and a `globalSetup` that
   intercepts `fetch` / `XMLHttpRequest` and fails on un-mocked calls.
2. **Time is injected.** Use `freezegun.freeze_time` / `pytest-freezer`
   for backend, vitest fake timers for frontend, `clock.install()` for
   playwright. Never read the wall clock.
3. **Deterministic random seeds.** Configure `hypothesis` profile;
   `Faker(); fake.seed_instance(42)`; `vi.useFakeTimers()`.
4. **Order-independent tests.** `pytest-randomly` is in `addopts`;
   reshuffle every run. If a test breaks under reshuffle, fix the fixture
   leak — do not pin the order.
5. **Eventually-consistent waits poll.** `wait_for(condition,
timeout=10)` / `expect.toHaveCount()` / `waitFor(...)` — never
   `time.sleep`.
6. **Shared resources are scope-aware.** testcontainers run
   session-scoped; per-test isolation uses transaction rollback. Fixtures
   default to `function` scope; `module` / `session` requires a
   `# Reason:` comment.
7. **CI test envs block egress.** `respx assert_all_called=True` and the
   vitest `globalSetup` intercept catch any un-mocked egress.
8. **No mutable globals across tests.** Never module-level mutable
   `list` / `dict`; never mutate `sys.modules`; use `monkeypatch`
   fixtures and let them auto-restore.
9. **Async / concurrency timeout cap.** `pytest-timeout=30` in
   `addopts`; playwright `test.setTimeout(30_000)`. Override must
   include a same-line comment explaining why.
10. **Time-sensitive flows inject clocks.** Reconciler waits use
    `wait_for`, never `sleep`. CI lint rejects `time.sleep` inside
    `backend/tests/`.
11. **Reproducible random failures.** On failure, hypothesis logs the
    seed; vitest prints `--seed`; playwright prints the worker index.
12. **CI auto-rerun is limited.** `pytest-rerunfailures --reruns=2`
    applies **only** to `@pytest.mark.flaky` tests. Unmarked failures
    never retry.

## Quarantine workflow
```

detect → mark (with issue link) → 14-day fix SLO → 21-day delete

````

A flaky-tracked test **must** carry both markers and a linked issue:

```python
@pytest.mark.flaky(reruns=2)
@pytest.mark.flaky_tracked(issue="https://github.com/bolin8017/lolday/issues/N")
def test_xxx():
    ...
````

`backend/tests/conftest.py` (the root one) installs a `pytest_collection_modifyitems`
hook that rejects `flaky_tracked` without an `issue` kwarg.

`flaky-tracker.yml` (weekly cron) aggregates the last 7 days of JUnit XML;
any test with failure rate > 1 % gets an auto-issue with the `flaky` label.
The original PR author is assigned. 14-day SLO triggers a Spidey Warnings
ping; 21-day SLO blocks CI on that test (re-fix or delete — never silently
disable).

**Delete the test, not the source code.** An unreliable test is worse than
no test.

## Pytest markers (registered in `backend/pyproject.toml`)

| Marker                                  | Use                                                                             |
| --------------------------------------- | ------------------------------------------------------------------------------- |
| `@pytest.mark.heavy`                    | Belongs to testcontainers slow tier; skipped in PR fast tier (`-m "not heavy"`) |
| `@pytest.mark.contract`                 | API / manifest contract test; runs serially in fast tier                        |
| `@pytest.mark.flaky_tracked(issue=...)` | Known flaky; requires issue URL; collection hook enforces                       |

`@pytest.mark.no_mock_mlflow` (existing) — keeps autouse MLflow off.

## Parallelization

`backend/pyproject.toml` `addopts` includes `-n auto --dist loadscope`.

- `loadscope` groups same-file tests on one worker; safe for aiosqlite per-file fixtures.
- `contract` tests are forced serial (schemathesis runs against a single FastAPI port).
- `heavy` tests use session-scoped testcontainers; `--dist loadgroup` keeps a test class on one worker.
- playwright stays `fullyParallel: false` until Phase 2 R4 (multi-persona) lands.

## Test execution telemetry

`test-telemetry.yml` (weekly cron) ingests `--junitxml` artifacts and writes
`docs/test-telemetry/dashboard.md` with P50/P95/P99 timings, 7-day failure
rate, and slow-test ranking. Use the dashboard to decide what to refactor
or retire.

## Per-area required tests

When you touch the listed area, the corresponding test type **must** be
present in the same PR. Path-filtered triggers in `dispatch.yml` enforce
this in CI:

| Touched path                            | Required additional test                  |
| --------------------------------------- | ----------------------------------------- |
| `backend/app/routers/*.py`              | contract/openapi schemathesis case        |
| `backend/app/reconciler/*.py`           | reconciler integration test               |
| `backend/migrations/*.py`               | up/down roundtrip + real-PG heavy migrate |
| `frontend/src/api/schema.gen.ts`        | contract/schema_gen_drift                 |
| `charts/lolday/templates/<resource>/**` | helm-unittest suite for `<resource>`      |
| `scripts/*.sh`                          | bats unit (after Phase 4)                 |

````

- [ ] **Step 2: pre-commit dry run**

```bash
pre-commit run --files .claude/rules/testing.md
````

Expected: prettier may reformat trailing whitespace; rerun until clean.

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/testing.md
git commit -m "docs(rules): add .claude/rules/testing.md (anti-flaky + quarantine SOP)"
```

---

### Task 2: Add backend dev dependencies (D1.3)

**Files:**

- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Append the new dev deps**

Locate the existing `[dependency-groups]` block in `backend/pyproject.toml`
and add to the `dev` group:

```toml
[dependency-groups]
dev = [
    # ... existing entries kept unchanged ...
    # NEW Phase 1 test deps
    "pytest-xdist>=3.6,<4",
    "pytest-timeout>=2.3,<3",
    "pytest-randomly>=3.15,<4",
    "pytest-rerunfailures>=14,<15",
    "pytest-split>=0.9,<1",
    "pytest-testmon>=2.1,<3",  # dev-only; not enabled in CI
    "freezegun>=1.5,<2",
    "hypothesis>=6.114,<7",
    "schemathesis>=3.36,<4",
    "testcontainers[postgres,minio]>=4.7,<5",
    "mlflow>=2.20,<3",  # for testcontainers real-MLflow tier
    "kubernetes-fake-client>=0.0.20",  # latest at writing
    "polyfactory>=2.18,<3",
]
```

- [ ] **Step 2: Refresh lock + sync**

```bash
cd backend
uv lock --refresh
uv sync
```

Expected: every new package resolves; `uv.lock` updates.

- [ ] **Step 3: Sanity — existing tests still collect**

```bash
cd backend
uv run pytest --collect-only 2>&1 | tail -3
```

Expected: `=== 96 tests collected in <X>s ===` (existing count
unchanged).

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "feat(backend): add Phase 1 test deps (pytest-xdist, hypothesis, schemathesis, testcontainers, polyfactory)"
```

---

### Task 3: Configure pytest addopts + markers (D1.4)

**Files:**

- Modify: `backend/pyproject.toml` `[tool.pytest.ini_options]`

- [ ] **Step 1: Replace the `[tool.pytest.ini_options]` block**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = "-n auto --dist loadscope --maxfail=10 --durations=20 --strict-markers --timeout=30"
markers = [
    "no_mock_mlflow: do not apply the MLflow autouse mock",
    "heavy: testcontainers slow tier; skipped by default in fast tier (-m 'not heavy')",
    "contract: API or manifest contract test (runs serial)",
    "flaky_tracked: known flaky test; must carry issue=<github-url> kwarg",
]
```

- [ ] **Step 2: Add the collection hook for `flaky_tracked` issue enforcement**

Open `backend/tests/conftest.py` and append at end (this is the existing
850-line file; the deep split happens in Task 6 onward — for now just
append):

```python
def pytest_collection_modifyitems(config, items):
    """Reject @pytest.mark.flaky_tracked without an issue URL."""
    for item in items:
        for marker in item.iter_markers(name="flaky_tracked"):
            issue = marker.kwargs.get("issue")
            if not issue or not issue.startswith("https://github.com/"):
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.flaky_tracked requires "
                    f"issue=<github-url> kwarg; got {issue!r}"
                )
```

(Import `pytest` at the top if not already imported.)

- [ ] **Step 3: Run the full suite — expect green**

```bash
cd backend
uv run pytest -q
```

Expected: 96 passed in < 90 s (xdist parallel speeds things up). If any
test relied on serial execution, it surfaces now — fix the fixture leak.

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/tests/conftest.py
git commit -m "feat(backend): pytest addopts + markers (xdist, timeout, strict-markers, flaky_tracked enforcement)"
```

---

### Task 4: Create the new test directory tree (D1.1 — part 1 of 2)

**Files:**

- Create: `backend/tests/{unit,integration,contract,heavy,factories}/__init__.py`
- Create: `backend/tests/unit/{auth,models,services,invariants}/__init__.py`
- Create: `backend/tests/integration/{routers,reconciler,services,migrations}/__init__.py`
- Create: `backend/tests/contract/{openapi,mlflow,volcano,harbor,discord}/__init__.py`
- Create: `backend/tests/heavy/{postgres,mlflow,minio,k8s_fake}/__init__.py`
- Create: `backend/tests/fixtures/mlflow/recorded/.gitkeep`

- [ ] **Step 1: Create directories + empty `__init__.py` files**

```bash
cd backend/tests
for d in \
  unit unit/auth unit/models unit/services unit/invariants \
  integration integration/routers integration/reconciler integration/services integration/migrations \
  contract contract/openapi contract/mlflow contract/volcano contract/harbor contract/discord \
  heavy heavy/postgres heavy/mlflow heavy/minio heavy/k8s_fake \
  factories fixtures/mlflow/recorded
do
  mkdir -p "$d"
  touch "$d/__init__.py"
done
touch fixtures/mlflow/recorded/.gitkeep
```

- [ ] **Step 2: Verify pytest still collects 96 tests**

```bash
cd backend
uv run pytest --collect-only 2>&1 | tail -3
```

Expected: `=== 96 tests collected ===` (new empty dirs don't add tests).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/
git commit -m "feat(backend/tests): create layered test directory tree (unit/integration/contract/heavy/factories)"
```

---

### Task 5: Move 96 existing tests into `integration/` subdirs (D1.1 — part 2 of 2)

**Files:**

- Move ~96 files from `backend/tests/test_*.py` into `backend/tests/integration/<subarea>/`

- [ ] **Step 1: Move tests by area**

Use `git mv` so history is preserved. The mapping below uses the area
prefix of each test name:

```bash
cd backend/tests
# Routers
git mv test_jobs.py             integration/routers/test_jobs.py
git mv test_jobs_*.py           integration/routers/      # all jobs-prefixed router/integration files
git mv test_detectors.py        integration/routers/test_detectors.py
git mv test_detectors_*.py      integration/routers/
git mv test_datasets.py         integration/routers/test_datasets.py
git mv test_admin.py            integration/routers/test_admin.py
git mv test_experiments_proxy.py            integration/routers/
git mv test_experiments_proxy_*.py          integration/routers/
git mv test_mlflow_authz.py                 integration/routers/test_mlflow_authz.py
git mv test_cf_access.py                    integration/routers/test_cf_access_router.py
git mv test_csrf_middleware.py              integration/routers/
git mv test_body_size_middleware.py         integration/routers/
git mv test_health_rate_limit.py            integration/routers/
git mv test_metrics.py                      integration/routers/test_metrics_router.py
git mv test_audit_log.py                    integration/routers/test_audit_log_router.py
git mv test_internal_events.py              integration/routers/
git mv test_credentials.py                  integration/routers/

# Models (ORM)
git mv test_models_*.py                     integration/services/  # currently service-shaped tests
git mv test_model_registry_resolver.py      integration/services/

# Reconciler
git mv reconciler/test_*.py                 integration/reconciler/  # existing tests/reconciler/ moves wholesale
rmdir reconciler

# Services
git mv services/test_*.py                   integration/services/
rmdir services

# Migrations
git mv test_migrations_*.py                 integration/migrations/

# Specific service-shaped tests
git mv test_jsonschema_validate_params.py   integration/services/
git mv test_build_clone_filter.py           integration/services/
git mv test_deadmans_switch_check.py        integration/services/
git mv test_detectors_description_sanitize.py integration/services/
git mv test_config_validation.py            integration/services/
```

(Any remaining `test_*.py` in `backend/tests/` root should be a strict
list — the only file that stays at root is `conftest.py`. Move
everything else into the matching `integration/<subarea>/`.)

- [ ] **Step 2: Verify pytest still collects 96**

```bash
cd backend
uv run pytest --collect-only 2>&1 | tail -3
```

Expected: `=== 96 tests collected ===`. If less, `git mv` missed a file.

- [ ] **Step 3: Run full suite — expect green**

```bash
cd backend
uv run pytest -q
```

Expected: 96 passed.

- [ ] **Step 4: Commit**

```bash
git add -A backend/tests/
git commit -m "refactor(backend/tests): move 96 existing tests into integration/<subarea>/ subdirs (D1.1)"
```

---

### Task 6: Split conftest — create `integration/conftest.py` (D1.2 / R1 — part 1 of 5)

**Files:**

- Create: `backend/tests/integration/conftest.py`
- Modify (temporary): `backend/tests/conftest.py` (the autouse mocks move out here)

- [ ] **Step 1: Identify what moves to `integration/conftest.py`**

From `backend/tests/conftest.py`, the following move to
`integration/conftest.py`:

- `mock_mlflow` autouse fixture (~400 lines) and its supporting
  `_MlflowMockState` class
- `fake_redis_for_rate_limit` autouse fixture
- `mock_k8s_batch` autouse fixture
- `_mock_k8s_load_config` autouse fixture
- All `respx`-related default fixtures
- The `_make_user` / `seed_user` / `_MINIMAL_MANIFEST` /
  `RICH_MANIFEST_WITH_TRAIN_DEFAULTS` helpers (these move to
  `factories/` in Task 9)

Imports the existing conftest pulls in for these — move with them.

- [ ] **Step 2: Create `integration/conftest.py`**

Open the existing `backend/tests/conftest.py` (850 lines) and cut the
sections listed above; paste them into a new file
`backend/tests/integration/conftest.py`. Preserve imports at top.

Add a header docstring:

```python
"""Integration-tier fixtures: aiosqlite + autouse mocks for K8s / MLflow /
Redis / Discord. Applies to backend/tests/integration/ tree only — heavy
and contract tiers use their own conftests under those directories."""
```

- [ ] **Step 3: Run integration tests only — expect green**

```bash
cd backend
uv run pytest tests/integration -q
```

Expected: all moved tests pass (autouse mocks now live in the
integration conftest).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/integration/conftest.py backend/tests/conftest.py
git commit -m "refactor(backend/tests): move autouse mocks (mlflow/k8s/redis) to integration/conftest.py (R1)"
```

---

### Task 7: Create `heavy/conftest.py` with testcontainers session lifecycle (D1.2 / R1 — part 2 of 5)

**Files:**

- Create: `backend/tests/heavy/conftest.py`

- [ ] **Step 1: Author the conftest**

```python
"""Heavy-tier fixtures: real Postgres / MLflow / MinIO containers via
testcontainers-python. Session-scoped startup; per-test isolation via
transaction rollback. Applies to backend/tests/heavy/ tree."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    # Reason: container boot is ~5s; session-scoped amortises across the heavy tier.
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_url(postgres_container: PostgresContainer) -> str:
    return postgres_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://", 1
    )


@pytest_asyncio.fixture(scope="session")
async def real_pg_engine(postgres_url: str):
    engine = create_async_engine(postgres_url, future=True, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def real_pg_session(real_pg_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test transaction with rollback at teardown — keeps tests independent."""
    async with real_pg_engine.connect() as conn:
        trans = await conn.begin()
        Session = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with Session() as session:
            yield session
        await trans.rollback()


@pytest.fixture(scope="session")
def minio_container() -> Generator[MinioContainer, None, None]:
    with MinioContainer() as minio:
        yield minio


@pytest.fixture(scope="session")
def mlflow_url() -> Generator[str, None, None]:
    """Spin up a real MLflow server in a container, backed by the session-scoped
    Postgres for tracking and MinIO for artifacts."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    mlflow = (
        DockerContainer("ghcr.io/mlflow/mlflow:v2.20.0")
        .with_command(
            "mlflow server "
            "--host 0.0.0.0 "
            "--port 5000 "
            "--backend-store-uri sqlite:////tmp/mlflow.db "
            "--default-artifact-root /tmp/artifacts"
        )
        .with_exposed_ports(5000)
    )
    mlflow.start()
    wait_for_logs(mlflow, "Listening at", timeout=30)
    yield f"http://{mlflow.get_container_host_ip()}:{mlflow.get_exposed_port(5000)}"
    mlflow.stop()
```

- [ ] **Step 2: Add a smoke test**

Create `backend/tests/heavy/postgres/test_smoke.py`:

```python
import pytest

pytestmark = pytest.mark.heavy


@pytest.mark.asyncio
async def test_real_pg_session_yields(real_pg_session):
    result = await real_pg_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
```

(Add `from sqlalchemy import text` import at top.)

- [ ] **Step 3: Run only heavy smoke**

```bash
cd backend
uv run pytest tests/heavy -m heavy -q
```

Expected: 1 passed (the smoke test); Postgres container starts once.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/heavy/
git commit -m "feat(backend/tests/heavy): add testcontainers Postgres/MinIO/MLflow session fixtures + smoke (R1)"
```

---

### Task 8: Create `contract/conftest.py` with schemathesis app loader (D1.2 / R1 — part 3 of 5)

**Files:**

- Create: `backend/tests/contract/conftest.py`

- [ ] **Step 1: Author the conftest**

```python
"""Contract-tier fixtures: schemathesis app loader + respx replay-tape loader.
Contract tests run **serial** (one FastAPI instance per process)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient

from app.main import app

REPLAY_TAPE_DIR = Path(__file__).parent.parent / "fixtures" / "mlflow" / "recorded"


@pytest.fixture(scope="session")
def fastapi_app():
    """Return the FastAPI app, with autouse-mocked dependencies disabled where needed."""
    return app


@pytest.fixture(scope="session")
def schema(fastapi_app):
    return schemathesis.from_asgi("/openapi.json", fastapi_app)


@pytest.fixture
def client(fastapi_app) -> TestClient:
    return TestClient(fastapi_app)


@pytest.fixture
def mlflow_replay_tape(request):
    """Load a recorded MLflow response tape by file name (e.g. 'create_run.json')."""
    tape_name = request.param if hasattr(request, "param") else None
    if not tape_name:
        return None
    with (REPLAY_TAPE_DIR / tape_name).open() as f:
        return json.load(f)
```

- [ ] **Step 2: Run contract dir — empty for now**

```bash
cd backend
uv run pytest tests/contract -q
```

Expected: `no tests ran` (no tests yet; conftest just loads).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/contract/conftest.py
git commit -m "feat(backend/tests/contract): add schemathesis app loader + replay-tape fixture (R1)"
```

---

### Task 9: Create polyfactory factories (D1.2 / R1 — part 4 of 5)

**Files:**

- Create: `backend/tests/factories/user_factory.py`
- Create: `backend/tests/factories/job_factory.py`
- Create: `backend/tests/factories/detector_factory.py`
- Create: `backend/tests/factories/dataset_factory.py`
- Modify: integration tests that used `_make_user` / `seed_user` to import the factories

- [ ] **Step 1: Author `user_factory.py`**

```python
"""polyfactory factories for User. Replaces the _make_user / seed_user
helpers from the old monolithic conftest."""

from __future__ import annotations

from polyfactory.factories import DataclassFactory
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory

from app.models.user import User


class UserFactory(SQLAlchemyFactory[User]):
    __model__ = User
    __set_relationships__ = False
    __set_foreign_keys__ = False

    @classmethod
    def admin(cls, **kwargs) -> User:
        return cls.build(role="admin", **kwargs)

    @classmethod
    def developer(cls, **kwargs) -> User:
        return cls.build(role="developer", **kwargs)

    @classmethod
    def regular(cls, **kwargs) -> User:
        return cls.build(role="user", **kwargs)
```

- [ ] **Step 2: Author `job_factory.py`, `detector_factory.py`, `dataset_factory.py`**

Follow the same pattern — one `SQLAlchemyFactory[<Model>]` per model with
convenience `.queued()`, `.running()`, `.succeeded()` class methods that
inject the right enum value.

```python
# backend/tests/factories/job_factory.py
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory
from app.models.job import Job, JobStatus, ResourceProfile


class JobFactory(SQLAlchemyFactory[Job]):
    __model__ = Job
    __set_relationships__ = False
    __set_foreign_keys__ = False

    @classmethod
    def queued(cls, **kwargs):
        return cls.build(status=JobStatus.QUEUED_BACKEND, **kwargs)

    @classmethod
    def running(cls, **kwargs):
        return cls.build(status=JobStatus.RUNNING, **kwargs)

    @classmethod
    def succeeded(cls, **kwargs):
        return cls.build(status=JobStatus.SUCCEEDED, **kwargs)
```

```python
# backend/tests/factories/detector_factory.py
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory
from app.models.detector import Detector, DetectorVersion


class DetectorFactory(SQLAlchemyFactory[Detector]):
    __model__ = Detector
    __set_relationships__ = False
    __set_foreign_keys__ = False


class DetectorVersionFactory(SQLAlchemyFactory[DetectorVersion]):
    __model__ = DetectorVersion
    __set_relationships__ = False
    __set_foreign_keys__ = False
```

```python
# backend/tests/factories/dataset_factory.py
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory
from app.models.dataset import Dataset, DatasetConfig


class DatasetFactory(SQLAlchemyFactory[Dataset]):
    __model__ = Dataset
    __set_relationships__ = False
    __set_foreign_keys__ = False


class DatasetConfigFactory(SQLAlchemyFactory[DatasetConfig]):
    __model__ = DatasetConfig
    __set_relationships__ = False
    __set_foreign_keys__ = False
```

- [ ] **Step 3: Migrate existing tests to import factories**

Find every call site of the old `_make_user` / `seed_user`:

```bash
cd backend
grep -rln '_make_user\|seed_user' tests/
```

In each file, replace:

```python
# before
user = _make_user(role="admin")

# after
from tests.factories.user_factory import UserFactory
user = UserFactory.admin()
```

- [ ] **Step 4: Run integration tests**

```bash
cd backend
uv run pytest tests/integration -q
```

Expected: 96 passed (or whatever count after Task 5).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/factories/ backend/tests/integration/
git commit -m "refactor(backend/tests): polyfactory factories replace _make_user / seed_user (R1)"
```

---

### Task 10: Slim root `conftest.py` to < 200 lines (D1.2 / R1 — part 5 of 5)

**Files:**

- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Inventory what remains**

What stays in the root `conftest.py`:

- `client` (anonymous FastAPI TestClient)
- Role-based `AsyncClient` variants (`auth_client_user`,
  `auth_client_developer`, `auth_client_admin`,
  `auth_client_service_token`, `user_client`, `second_user_client`,
  `internal_client`)
- `setup_db` (autouse, aiosqlite per test)
- `db_session`, `test_session_maker`
- The base `mock_k8s_batch` skeleton (without the rich Volcano CRD logic —
  that moved to integration)
- The `pytest_collection_modifyitems` hook from Task 3

What moves (already done in Tasks 6–9): autouse MLflow / Redis / K8s
detail mocks; `_make_user` / `seed_user`; manifest constants.

- [ ] **Step 2: Edit the file**

Open `backend/tests/conftest.py` and delete every section already moved.
The result should be ≤ 200 lines and contain only the items in the
inventory above. Keep imports clean.

- [ ] **Step 3: Verify line count**

```bash
wc -l backend/tests/conftest.py
```

Expected: < 200.

- [ ] **Step 4: Full suite still green**

```bash
cd backend
uv run pytest -q
```

Expected: 96 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "refactor(backend/tests): slim root conftest.py to <200 lines (R1 complete)"
```

---

### Task 11: `MlflowClient` becomes a class (D1.5 / R2 — part 1 of 3)

**Files:**

- Modify: `backend/app/services/mlflow_client.py`

- [ ] **Step 1: Refactor module to class**

Replace the entire module with:

```python
"""MLflow REST client. Instantiated once in app/main.py lifespan;
injected via Depends(get_mlflow). No module-level singleton — that was
hostile to heavy-tier real-MLflow tests (R2)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class MlflowClient:
    def __init__(self, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client

    @classmethod
    def from_settings(cls, settings: Settings, http_client: httpx.AsyncClient) -> "MlflowClient":
        return cls(settings.MLFLOW_TRACKING_URL, http_client)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base_url}{path}"
        response = await self._http.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    async def create_run(self, experiment_id: str, **fields: Any) -> dict:
        body = {"experiment_id": experiment_id, **fields}
        resp = await self._request("POST", "/api/2.0/mlflow/runs/create", json=body)
        return resp.json()["run"]

    async def terminate_run(self, run_id: str, status: str, end_time: int) -> None:
        body = {"run_id": run_id, "status": status, "end_time": end_time}
        await self._request("POST", "/api/2.0/mlflow/runs/update", json=body)

    # Migrate every other public function the module used to expose into a
    # method here. The signatures stay the same except `self` is added.
```

(Translate every function in the existing
`backend/app/services/mlflow_client.py` to a method on the class. Keep
the doc strings.)

- [ ] **Step 2: Verify the module imports cleanly**

```bash
cd backend
uv run python -c "from app.services.mlflow_client import MlflowClient; print(MlflowClient)"
```

Expected: `<class 'app.services.mlflow_client.MlflowClient'>`.

- [ ] **Step 3: Run the full suite — expect FAILures**

```bash
cd backend
uv run pytest -q 2>&1 | tail -5
```

Expected: many `ImportError` or `AttributeError` — the callers still use
the old function names. Task 12 and Task 13 fix the callers.

- [ ] **Step 4: Commit (intentionally red — gate by R2 Task 13)**

```bash
git add backend/app/services/mlflow_client.py
git commit -m "refactor(services): MlflowClient becomes a class — callers migrated in next task (R2 step 1/3)"
```

(Note: this commit intentionally breaks `main`. The next two tasks land
back-to-back and the branch is not pushed until all three are done.
Alternatively, do Tasks 11–13 in a single PR.)

---

### Task 12: Wire `MlflowClient` into the FastAPI lifespan + add `Depends` (D1.5 / R2 — part 2 of 3)

**Files:**

- Modify: `backend/app/main.py`
- Modify: `backend/app/deps.py`

- [ ] **Step 1: Edit `app/main.py` lifespan**

```python
# app/main.py — inside lifespan
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings
from app.services.mlflow_client import MlflowClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
        app.state.http = http
        app.state.mlflow = MlflowClient.from_settings(settings, http)
        # ... any existing lifespan setup ...
        yield
        # ... any existing teardown ...
```

(If `app/main.py` already has a `lifespan` function, merge the
`app.state.mlflow = ...` line in alongside the existing setup.)

- [ ] **Step 2: Edit `app/deps.py`**

Append:

```python
from fastapi import Request

from app.services.mlflow_client import MlflowClient


def get_mlflow(request: Request) -> MlflowClient:
    return request.app.state.mlflow
```

- [ ] **Step 3: Sanity check the app starts**

```bash
cd backend
uv run python -c "from app.main import app; print(app)"
```

Expected: prints `<fastapi.FastAPI ...>` without import errors.

- [ ] **Step 4: Commit (still red until Task 13)**

```bash
git add backend/app/main.py backend/app/deps.py
git commit -m "feat(app): wire MlflowClient through lifespan + add get_mlflow Depends (R2 step 2/3)"
```

---

### Task 13: Migrate callers to `Depends(get_mlflow)` (D1.5 / R2 — part 3 of 3)

**Files:**

- Modify: `backend/app/routers/jobs.py`
- Modify: `backend/app/routers/experiments_proxy.py`
- Modify: `backend/app/reconciler/jobs.py`
- Modify: `backend/tests/integration/conftest.py` (override `get_mlflow` so existing tests keep passing)

- [ ] **Step 1: Routers**

For every function in `routers/jobs.py` and `routers/experiments_proxy.py`
that called the old module-level functions, add a parameter and call the
method:

```python
# before
from app.services import mlflow_client
await mlflow_client.create_run(experiment_id=..., ...)

# after
from app.deps import get_mlflow
from app.services.mlflow_client import MlflowClient
from fastapi import Depends

@router.post(...)
async def create_job(..., mlflow: MlflowClient = Depends(get_mlflow)):
    ...
    run = await mlflow.create_run(experiment_id=..., ...)
```

- [ ] **Step 2: Reconciler**

`reconciler/jobs.py` is not a FastAPI handler, so it cannot use
`Depends`. Pass the instance in:

```python
# app/reconciler/loop.py
from app.services.mlflow_client import MlflowClient

async def reconciler_loop(stop_event, mlflow: MlflowClient):
    ...

# app/reconciler/jobs.py
async def reconcile_job(session, job, mlflow: MlflowClient):
    ...
    await mlflow.terminate_run(run_id=job.mlflow_run_id, status=..., end_time=...)
```

In `app/main.py` lifespan, pass `app.state.mlflow` to
`reconciler_loop(stop_event, app.state.mlflow)`.

- [ ] **Step 3: Test override**

Open `backend/tests/integration/conftest.py` and add (next to the autouse
MLflow mock):

```python
from app.deps import get_mlflow
from app.main import app as fastapi_app


@pytest.fixture(autouse=True)
def override_mlflow_dep(mock_mlflow):
    """Route Depends(get_mlflow) to the autouse MLflow mock so existing
    integration tests keep their original behaviour."""
    fastapi_app.dependency_overrides[get_mlflow] = lambda: mock_mlflow
    yield
    fastapi_app.dependency_overrides.pop(get_mlflow, None)
```

(Where `mock_mlflow` is the existing autouse mock object — the integration
mock must expose the same methods as `MlflowClient`: `create_run`,
`terminate_run`, etc. If it doesn't, expand the mock to match the class
surface.)

- [ ] **Step 4: Full suite green**

```bash
cd backend
uv run pytest -q
```

Expected: 96 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/ backend/app/reconciler/ backend/tests/integration/conftest.py
git commit -m "refactor(app): migrate MlflowClient callers to Depends(get_mlflow) (R2 step 3/3)"
```

---

### Task 14: schemathesis contract test for `/api/v1/jobs` (D1.7 — 1 of 5)

**Files:**

- Create: `backend/tests/contract/openapi/test_schemathesis_jobs.py`

- [ ] **Step 1: Author the test**

```python
"""Property-based schema test for /api/v1/jobs endpoints. Derived from
/openapi.json by schemathesis; runs hundreds of generated cases per
endpoint to catch shape drift between OpenAPI spec and actual handler
responses."""

import pytest
import schemathesis

pytestmark = pytest.mark.contract

schema = schemathesis.from_path("backend/openapi.json")  # generated by app


@schema.parametrize(endpoint="/api/v1/jobs")
def test_jobs_endpoints_match_schema(case, client):
    """Every generated payload either succeeds per spec or is rejected
    with a documented status code."""
    response = case.call(session=client)
    case.validate_response(response)
```

If the OpenAPI file isn't pre-generated, instead use the ASGI loader from
`tests/contract/conftest.py`:

```python
from tests.contract.conftest import fastapi_app

schema = schemathesis.from_asgi("/openapi.json", fastapi_app)


@schema.parametrize(endpoint="/api/v1/jobs")
def test_jobs_endpoints_match_schema(case):
    response = case.call_asgi()
    case.validate_response(response)
```

- [ ] **Step 2: Run it**

```bash
cd backend
uv run pytest tests/contract/openapi/test_schemathesis_jobs.py -v
```

Expected: schemathesis generates ~50 cases per endpoint; all pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/contract/openapi/test_schemathesis_jobs.py
git commit -m "test(contract): schemathesis property test for /api/v1/jobs endpoints (D1.7)"
```

---

### Task 15: schemathesis contract test for `/api/v1/detectors` (D1.7 — 2 of 5)

**Files:**

- Create: `backend/tests/contract/openapi/test_schemathesis_detectors.py`

- [ ] **Step 1: Author the test**

```python
import pytest
import schemathesis

from tests.contract.conftest import fastapi_app

pytestmark = pytest.mark.contract

schema = schemathesis.from_asgi("/openapi.json", fastapi_app)


@schema.parametrize(endpoint="/api/v1/detectors")
def test_detectors_endpoints_match_schema(case):
    response = case.call_asgi()
    case.validate_response(response)
```

- [ ] **Step 2: Run**

```bash
cd backend
uv run pytest tests/contract/openapi/test_schemathesis_detectors.py -v
```

Expected: all generated cases pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/contract/openapi/test_schemathesis_detectors.py
git commit -m "test(contract): schemathesis property test for /api/v1/detectors endpoints (D1.7)"
```

---

### Task 16: schemathesis contract test for `/api/v1/users/me` (D1.7 — 3 of 5)

**Files:**

- Create: `backend/tests/contract/openapi/test_schemathesis_users_me.py`

- [ ] **Step 1: Author the test**

```python
import pytest
import schemathesis

from tests.contract.conftest import fastapi_app

pytestmark = pytest.mark.contract

schema = schemathesis.from_asgi("/openapi.json", fastapi_app)


@schema.parametrize(endpoint="/api/v1/users/me")
def test_users_me_match_schema(case):
    response = case.call_asgi()
    case.validate_response(response)
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/contract/openapi/test_schemathesis_users_me.py -v
git add backend/tests/contract/openapi/test_schemathesis_users_me.py
git commit -m "test(contract): schemathesis property test for /api/v1/users/me (D1.7)"
```

---

### Task 17: MLflow respx replay tape contract test (D1.7 — 4 of 5)

**Files:**

- Create: `backend/tests/fixtures/mlflow/recorded/create_run.json`
- Create: `backend/tests/fixtures/mlflow/recorded/terminate_run.json`
- Create: `backend/tests/fixtures/mlflow/recorded/get_run.json`
- Create: `backend/tests/contract/mlflow/test_mlflow_response_shape.py`

- [ ] **Step 1: Record golden responses from a real MLflow instance**

Spin a one-off `mlflow/mlflow:v2.20.0` container and capture three
representative responses:

```bash
docker run --rm -d --name mlflow-record -p 5000:5000 \
  ghcr.io/mlflow/mlflow:v2.20.0 \
  mlflow server --host 0.0.0.0 --port 5000 \
    --backend-store-uri sqlite:////tmp/mlflow.db \
    --default-artifact-root /tmp/artifacts

sleep 5
curl -s -X POST http://localhost:5000/api/2.0/mlflow/experiments/create \
  -H 'Content-Type: application/json' \
  -d '{"name": "exp-1"}'
# Take note of experiment_id

curl -s -X POST http://localhost:5000/api/2.0/mlflow/runs/create \
  -H 'Content-Type: application/json' \
  -d '{"experiment_id": "<id>"}' | tee tests/fixtures/mlflow/recorded/create_run.json

# Repeat for /runs/update and /runs/get with their canonical request bodies

docker rm -f mlflow-record
```

The recorded JSON files become the golden shape — any future MLflow API
change that breaks our caller now breaks this test in CI.

- [ ] **Step 2: Author the contract test**

```python
"""Contract test against MLflow REST API shape. Uses respx to mock the
network layer with previously-recorded responses (see
backend/tests/fixtures/mlflow/recorded/). Fails if our MlflowClient
class is unable to parse the recorded shapes."""

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.services.mlflow_client import MlflowClient

pytestmark = pytest.mark.contract

TAPE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "mlflow" / "recorded"


@pytest.mark.asyncio
async def test_create_run_parses_recorded_response():
    with open(TAPE_DIR / "create_run.json") as f:
        recorded = json.load(f)

    async with respx.mock() as mock:
        mock.post("http://mlflow.test/api/2.0/mlflow/runs/create").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        async with httpx.AsyncClient() as http:
            client = MlflowClient("http://mlflow.test", http)
            run = await client.create_run(experiment_id="0")

    assert run["info"]["run_id"]
    assert run["info"]["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_terminate_run_succeeds_against_recorded():
    with open(TAPE_DIR / "terminate_run.json") as f:
        recorded = json.load(f)

    async with respx.mock() as mock:
        mock.post("http://mlflow.test/api/2.0/mlflow/runs/update").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        async with httpx.AsyncClient() as http:
            client = MlflowClient("http://mlflow.test", http)
            # terminate_run returns None; assert that the recorded response
            # parses without raising.
            await client.terminate_run(
                run_id="r0", status="FINISHED", end_time=1234567890
            )


@pytest.mark.asyncio
async def test_get_run_parses_recorded_response():
    with open(TAPE_DIR / "get_run.json") as f:
        recorded = json.load(f)

    async with respx.mock() as mock:
        mock.get("http://mlflow.test/api/2.0/mlflow/runs/get").mock(
            return_value=httpx.Response(200, json=recorded)
        )
        async with httpx.AsyncClient() as http:
            client = MlflowClient("http://mlflow.test", http)
            run = await client.get_run(run_id="r0")

    assert run["info"]["run_id"]
    assert run["info"]["status"] in {"RUNNING", "FINISHED", "FAILED"}
```

- [ ] **Step 3: Run**

```bash
cd backend
uv run pytest tests/contract/mlflow/test_mlflow_response_shape.py -v
```

Expected: 3 tests pass (one per recorded endpoint).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fixtures/mlflow/recorded/ backend/tests/contract/mlflow/
git commit -m "test(contract): MLflow REST shape — respx replay against recorded golden tapes (D1.7)"
```

---

### Task 18: kubeconform vcjob manifest contract test (D1.7 — 5 of 5)

**Files:**

- Create: `backend/tests/contract/volcano/test_vcjob_manifest_kubeconform.py`

- [ ] **Step 1: Author the test**

```python
"""Contract test for the Volcano vcjob manifest produced by
services/job_spec.py. kubeconform validates against the rendered manifest
schema. Catches Volcano CRD-version drift and required-field omissions."""

import json
import shutil
import subprocess

import pytest

from app.services.job_spec import build_vcjob_manifest
from tests.factories.job_factory import JobFactory

pytestmark = pytest.mark.contract

KUBECONFORM = shutil.which("kubeconform") or "kubeconform"


@pytest.mark.parametrize("status", ["queued", "running", "succeeded"])
def test_vcjob_manifest_passes_kubeconform(status, tmp_path):
    """Every manifest the builder produces validates against the bundled
    Volcano CRD schema."""
    job = getattr(JobFactory, status)()
    manifest = build_vcjob_manifest(job)

    manifest_file = tmp_path / "vcjob.yaml"
    manifest_file.write_text(json.dumps(manifest))  # kubeconform accepts JSON

    result = subprocess.run(
        [
            KUBECONFORM,
            "-strict",
            "-schema-location",
            "default",
            "-schema-location",
            "https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/{{.NormalizedKubernetesVersion}}-standalone-strict/{{.ResourceKind}}-{{.ResourceAPIVersion}}.json",
            str(manifest_file),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"kubeconform failed: {result.stdout}{result.stderr}"
```

- [ ] **Step 2: Install `kubeconform` if missing**

```bash
go install github.com/yannh/kubeconform/cmd/kubeconform@v0.6.7
# or download the prebuilt binary into ~/.local/bin/
```

- [ ] **Step 3: Run**

```bash
cd backend
uv run pytest tests/contract/volcano/test_vcjob_manifest_kubeconform.py -v
```

Expected: 3 parametrize cases pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/contract/volcano/
git commit -m "test(contract): kubeconform validates Volcano vcjob manifest (D1.7)"
```

---

### Task 19: heavy/postgres concurrent submit (D1.8 — 1 of 4)

**Files:**

- Create: `backend/tests/heavy/postgres/test_jobs_concurrent_submit.py`

- [ ] **Step 1: Author the test**

```python
"""Real-Postgres test: two users submitting the same detector
simultaneously must produce FIFO-correct queue positions with no row-lock
race. aiosqlite cannot reproduce this because it serializes writes."""

import asyncio

import pytest
from sqlalchemy import select

from app.models.job import Job
from tests.factories.job_factory import JobFactory

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


async def test_concurrent_submit_assigns_distinct_queue_positions(real_pg_session):
    # 50 jobs in flight at once
    jobs = [JobFactory.queued() for _ in range(50)]

    async def add_one(job):
        real_pg_session.add(job)
        await real_pg_session.flush()
        return job.id

    ids = await asyncio.gather(*(add_one(j) for j in jobs))

    assert len(set(ids)) == 50, "duplicate primary keys despite concurrent flush"

    result = await real_pg_session.execute(
        select(Job.id).order_by(Job.submitted_at, Job.id)
    )
    ordered = result.scalars().all()
    assert len(ordered) == 50
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/heavy/postgres/test_jobs_concurrent_submit.py -m heavy -v
git add backend/tests/heavy/postgres/test_jobs_concurrent_submit.py
git commit -m "test(heavy): real-Postgres concurrent-submit FIFO race (D1.8)"
```

---

### Task 20: heavy/postgres migrations up/down roundtrip (D1.8 — 2 of 4)

**Files:**

- Create: `backend/tests/heavy/postgres/test_migrations_real_pg.py`

- [ ] **Step 1: Author the test**

```python
"""Run every Alembic revision forward, then every revision backward, on a
real Postgres container. Catches downgrade scripts that were never
executed (the current `test_migrations_*.py` files only check forward
schema parity)."""

import subprocess

import pytest
from alembic.config import Config
from alembic import command

pytestmark = pytest.mark.heavy


@pytest.fixture
def alembic_cfg(postgres_url):
    cfg = Config("backend/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", postgres_url.replace("+asyncpg", ""))
    return cfg


def test_upgrade_to_head_then_downgrade_to_base(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    # If any downgrade is missing or invalid, command.downgrade raises.


def test_upgrade_to_head_is_idempotent(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    command.upgrade(alembic_cfg, "head")  # second run must no-op


def test_each_revision_round_trips(alembic_cfg):
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    revisions = [rev.revision for rev in script.walk_revisions()]
    for rev in reversed(revisions):  # forward then backward, one at a time
        command.upgrade(alembic_cfg, rev)
    for rev in revisions:
        command.downgrade(alembic_cfg, "-1")
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/heavy/postgres/test_migrations_real_pg.py -m heavy -v
git add backend/tests/heavy/postgres/test_migrations_real_pg.py
git commit -m "test(heavy): real-Postgres Alembic up/down roundtrip + idempotency (D1.8)"
```

---

### Task 21: heavy/mlflow real-MLflow lifecycle (D1.8 — 3 of 4)

**Files:**

- Create: `backend/tests/heavy/mlflow/test_real_mlflow_lifecycle.py`

- [ ] **Step 1: Author the test**

```python
"""End-to-end exercise of our MlflowClient against a real MLflow server
container. Verifies create → log → terminate cycle and surfaces any
MLflow REST contract drift."""

import time

import httpx
import pytest

from app.services.mlflow_client import MlflowClient

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


async def test_full_run_lifecycle(mlflow_url):
    async with httpx.AsyncClient() as http:
        client = MlflowClient(mlflow_url, http)

        # 1. Create experiment + run
        exp = await client.create_experiment(name=f"test-{int(time.time())}")
        run = await client.create_run(experiment_id=exp["experiment_id"])
        run_id = run["info"]["run_id"]

        # 2. Log a metric + a param
        await client.log_metric(run_id=run_id, key="auc", value=0.95)
        await client.log_param(run_id=run_id, key="model_type", value="random_forest")

        # 3. Terminate
        await client.terminate_run(
            run_id=run_id,
            status="FINISHED",
            end_time=int(time.time() * 1000),
        )

        # 4. Read back
        fetched = await client.get_run(run_id=run_id)
        assert fetched["info"]["status"] == "FINISHED"
        assert fetched["data"]["metrics"][0]["value"] == 0.95
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/heavy/mlflow/test_real_mlflow_lifecycle.py -m heavy -v
git add backend/tests/heavy/mlflow/test_real_mlflow_lifecycle.py
git commit -m "test(heavy): real MLflow create/log/terminate/get lifecycle (D1.8)"
```

---

### Task 22: heavy/k8s_fake Volcano vcjob full lifecycle (D1.8 — 4 of 4)

**Files:**

- Create: `backend/tests/heavy/k8s_fake/test_volcano_full_lifecycle.py`

- [ ] **Step 1: Author the test**

```python
"""Simulate the full Volcano vcjob lifecycle (Pending → Running → Completed)
via kubernetes-fake-client. Verifies our reconciler updates the DB Job row
through every legal transition."""

import pytest
from kubernetes_fake_client import FakeApiClient

from app.reconciler.jobs import reconcile_job
from app.models.job import JobStatus
from tests.factories.job_factory import JobFactory

pytestmark = [pytest.mark.heavy, pytest.mark.asyncio]


async def test_pending_to_completed_lifecycle(real_pg_session):
    fake = FakeApiClient()
    job = JobFactory.queued()
    real_pg_session.add(job)
    await real_pg_session.flush()

    # Stage 1: vcjob exists in Pending
    fake.custom_objects.create_namespaced_custom_object(
        group="batch.volcano.sh", version="v1alpha1", namespace="lolday-jobs",
        plural="jobs", body={
            "apiVersion": "batch.volcano.sh/v1alpha1",
            "kind": "Job",
            "metadata": {"name": f"vcjob-{job.id}", "namespace": "lolday-jobs"},
            "status": {"state": {"phase": "Pending"}},
        },
    )
    await reconcile_job(real_pg_session, job, fake, mlflow=None)
    assert job.status == JobStatus.PREPARING

    # Stage 2: vcjob Running
    fake.custom_objects.patch_namespaced_custom_object(
        group="batch.volcano.sh", version="v1alpha1", namespace="lolday-jobs",
        plural="jobs", name=f"vcjob-{job.id}",
        body={"status": {"state": {"phase": "Running"}}},
    )
    await reconcile_job(real_pg_session, job, fake, mlflow=None)
    assert job.status == JobStatus.RUNNING

    # Stage 3: vcjob Completed
    fake.custom_objects.patch_namespaced_custom_object(
        group="batch.volcano.sh", version="v1alpha1", namespace="lolday-jobs",
        plural="jobs", name=f"vcjob-{job.id}",
        body={"status": {"state": {"phase": "Completed"}}},
    )
    await reconcile_job(real_pg_session, job, fake, mlflow=None)
    assert job.status == JobStatus.SUCCEEDED
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/heavy/k8s_fake/test_volcano_full_lifecycle.py -m heavy -v
git add backend/tests/heavy/k8s_fake/
git commit -m "test(heavy): kubernetes-fake-client Volcano vcjob full lifecycle (D1.8)"
```

---

### Task 23: hypothesis JobStatus state-machine invariant (D1.14 — 1 of 2)

**Files:**

- Create: `backend/tests/unit/invariants/test_job_status_state_machine.py`

- [ ] **Step 1: Author the test**

```python
"""Property-based test of the JobStatus state machine. Every illegal
transition must raise; every legal transition must succeed. New enum
values added without updating the transition table fail this test
immediately."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.models.job import JobStatus, LEGAL_TRANSITIONS  # add LEGAL_TRANSITIONS in models/job.py if not present


all_states = st.sampled_from(list(JobStatus))


@given(src=all_states, dst=all_states)
def test_transition_legality(src: JobStatus, dst: JobStatus):
    """Either the transition is in LEGAL_TRANSITIONS or attempting it raises."""
    if (src, dst) in LEGAL_TRANSITIONS or src == dst:
        # legal: must not raise
        from app.models.job import assert_transition_legal
        assert_transition_legal(src, dst)  # raises on illegal
    else:
        with pytest.raises(ValueError, match="illegal transition"):
            from app.models.job import assert_transition_legal
            assert_transition_legal(src, dst)


def test_every_terminal_status_has_no_outgoing_legal_transition():
    terminals = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT}
    for term in terminals:
        outgoing = [dst for (src, dst) in LEGAL_TRANSITIONS if src == term]
        assert outgoing == [], f"{term} should be terminal but has {outgoing}"
```

- [ ] **Step 2: Ensure `LEGAL_TRANSITIONS` and `assert_transition_legal` exist in `app/models/job.py`**

If they don't, add:

```python
# app/models/job.py
LEGAL_TRANSITIONS = frozenset({
    (JobStatus.QUEUED_BACKEND, JobStatus.PREPARING),
    (JobStatus.PREPARING, JobStatus.RUNNING),
    (JobStatus.PREPARING, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    (JobStatus.RUNNING, JobStatus.FAILED),
    (JobStatus.RUNNING, JobStatus.TIMEOUT),
})


def assert_transition_legal(src: JobStatus, dst: JobStatus) -> None:
    if src == dst:
        return
    if (src, dst) not in LEGAL_TRANSITIONS:
        raise ValueError(f"illegal transition {src} → {dst}")
```

Wire `assert_transition_legal(old, new)` into the reconciler before every
status write.

- [ ] **Step 3: Run + Commit**

```bash
cd backend
uv run pytest tests/unit/invariants/test_job_status_state_machine.py -v
git add backend/tests/unit/invariants/ backend/app/models/job.py
git commit -m "test(invariants): hypothesis JobStatus state-machine legality (D1.14)"
```

---

### Task 24: hypothesis ResourceProfile enum totality (D1.14 — 2 of 2)

**Files:**

- Create: `backend/tests/unit/invariants/test_resource_profile_enum_totality.py`

- [ ] **Step 1: Author the test**

```python
"""Every ResourceProfile enum value must have a corresponding entry in
_RESOURCE_PROFILE_GPU_COUNT. The existing import-time assert catches this
on app boot; this test catches it during test collection (faster
feedback)."""

from hypothesis import given
from hypothesis import strategies as st

from app.models.job import ResourceProfile, _RESOURCE_PROFILE_GPU_COUNT

all_profiles = st.sampled_from(list(ResourceProfile))


@given(profile=all_profiles)
def test_every_profile_has_gpu_count(profile: ResourceProfile):
    assert profile in _RESOURCE_PROFILE_GPU_COUNT


def test_keys_match_enum_set():
    assert set(_RESOURCE_PROFILE_GPU_COUNT.keys()) == set(ResourceProfile)
```

- [ ] **Step 2: Run + Commit**

```bash
cd backend
uv run pytest tests/unit/invariants/test_resource_profile_enum_totality.py -v
git add backend/tests/unit/invariants/test_resource_profile_enum_totality.py
git commit -m "test(invariants): hypothesis ResourceProfile enum totality (D1.14)"
```

---

### Task 25: `values-test.yaml` + helm-unittest plugin in CI (D1.9 — 1 of 7)

**Files:**

- Create: `charts/lolday/values-test.yaml`
- Modify: `.github/workflows/helm.yml`

- [ ] **Step 1: Author `values-test.yaml`**

```yaml
# charts/lolday/values-test.yaml
# Minimal renderable values for helm-unittest. Fake secrets, monitoring
# off. Never used for production rendering.

global:
  domain: test.lolday.svc

backend:
  image:
    repository: ghcr.io/bolin8017/lolday-backend
    tag: test
  env:
    DATABASE_URL: "postgresql://test:test@localhost:5432/test"
    REDIS_URL: "redis://localhost:6379/0"
    MLFLOW_TRACKING_URL: "http://mlflow:5000"
    CF_ACCESS_AUD: "test-aud"
    CF_ACCESS_TEAM_DOMAIN: "test.cloudflareaccess.com"

frontend:
  image:
    repository: ghcr.io/bolin8017/lolday-frontend
    tag: test

monitoring:
  enabled: false

mlflow:
  enabled: false

harbor:
  enabled: false

minio:
  enabled: false

samples:
  hostPath: /tmp/test-samples
```

- [ ] **Step 2: Add helm-unittest step to `helm.yml`**

Append to `.github/workflows/helm.yml` after the existing `helm lint`
step:

```yaml
- name: Install helm-unittest plugin
  run: helm plugin install https://github.com/helm-unittest/helm-unittest --version 0.5.2

- name: Run helm-unittest suites
  run: |
    cd charts/lolday
    helm unittest -f 'tests/*_test.yaml' .
```

- [ ] **Step 3: Verify locally**

```bash
helm plugin install https://github.com/helm-unittest/helm-unittest --version 0.5.2
cd charts/lolday
helm unittest -f 'tests/*_test.yaml' .  # passes with 0 suites (suites land in Tasks 26–31)
```

Expected: `0 suites, 0 tests, 0 errors` (no suites yet).

- [ ] **Step 4: Commit**

```bash
git add charts/lolday/values-test.yaml .github/workflows/helm.yml
git commit -m "feat(charts,ci): helm-unittest plugin + values-test.yaml (D1.9 setup)"
```

---

### Task 26: helm-unittest — `backend_deployment_test.yaml` (D1.9 — 2 of 7)

**Files:**

- Create: `charts/lolday/tests/backend_deployment_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: backend deployment
templates:
  - templates/backend.yaml
release:
  name: lolday
  namespace: lolday
chart:
  version: 0.0.0
  appVersion: test
tests:
  - it: renders a Deployment
    asserts:
      - hasDocuments:
          count: 1
      - isKind:
          of: Deployment
      - equal:
          path: metadata.name
          value: backend

  - it: uses the backend image from values
    set:
      backend.image.repository: ghcr.io/bolin8017/lolday-backend
      backend.image.tag: v1.2.3
    asserts:
      - equal:
          path: spec.template.spec.containers[0].image
          value: ghcr.io/bolin8017/lolday-backend:v1.2.3

  - it: drops all capabilities and runs non-root
    asserts:
      - equal:
          path: spec.template.spec.containers[0].securityContext.runAsNonRoot
          value: true
      - equal:
          path: spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation
          value: false
      - contains:
          path: spec.template.spec.containers[0].securityContext.capabilities.drop
          content: ALL

  - it: injects DATABASE_URL from Secret
    asserts:
      - contains:
          path: spec.template.spec.containers[0].envFrom
          content:
            secretRef:
              name: lolday-backend
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/backend_deployment_test.yaml' .
# Expected: 4 tests passed

git add charts/lolday/tests/backend_deployment_test.yaml
git commit -m "test(charts): helm-unittest backend Deployment template (D1.9)"
```

---

### Task 27: helm-unittest — `networkpolicy_test.yaml` (D1.9 — 3 of 7)

**Files:**

- Create: `charts/lolday/tests/networkpolicy_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: backend NetworkPolicy
templates:
  - templates/networkpolicy/backend.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: ingress allows Traefik only (per feedback_traefik_is_np_source memory)
    asserts:
      - isKind:
          of: NetworkPolicy
      - contains:
          path: spec.ingress[0].from
          content:
            namespaceSelector:
              matchLabels:
                kubernetes.io/metadata.name: kube-system
      - contains:
          path: spec.ingress[0].from
          content:
            podSelector:
              matchLabels:
                app.kubernetes.io/name: traefik

  - it: egress allows DNS, Postgres, Redis, MLflow, MinIO
    asserts:
      - lengthEqual:
          path: spec.egress
          count: 5 # adjust to actual count after auditing templates/networkpolicy/backend.yaml

  - it: does NOT allow cloudflared as a source (only Traefik is)
    asserts:
      - notContains:
          path: spec.ingress[0].from
          content:
            podSelector:
              matchLabels:
                app.kubernetes.io/name: cloudflared
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/networkpolicy_test.yaml' .
git add charts/lolday/tests/networkpolicy_test.yaml
git commit -m "test(charts): helm-unittest backend NetworkPolicy traefik-source invariant (D1.9)"
```

---

### Task 28: helm-unittest — `kyverno_policy_test.yaml` (D1.9 — 4 of 7)

**Files:**

- Create: `charts/lolday/tests/kyverno_policy_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: Kyverno ClusterPolicies
templates:
  - templates/kyverno/verify-lolday-image-signatures.yaml
  - templates/kyverno/verify-lolday-harbor-image-signatures.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: GHCR ClusterPolicy is in enforce mode
    template: templates/kyverno/verify-lolday-image-signatures.yaml
    asserts:
      - isKind:
          of: ClusterPolicy
      - equal:
          path: spec.validationFailureAction
          value: Enforce

  - it: Harbor ClusterPolicy is in enforce mode
    template: templates/kyverno/verify-lolday-harbor-image-signatures.yaml
    asserts:
      - equal:
          path: spec.validationFailureAction
          value: Enforce

  - it: GHCR ClusterPolicy verifies keyless cosign
    template: templates/kyverno/verify-lolday-image-signatures.yaml
    asserts:
      - matchRegex:
          path: spec.rules[0].verifyImages[0].attestors[0].entries[0].keyless.subject
          pattern: ".*bolin8017/lolday.*"
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/kyverno_policy_test.yaml' .
git add charts/lolday/tests/kyverno_policy_test.yaml
git commit -m "test(charts): helm-unittest Kyverno enforce-mode invariant (D1.9)"
```

---

### Task 29: helm-unittest — `monitoring_alertrules_test.yaml` (D1.9 — 5 of 7)

**Files:**

- Create: `charts/lolday/tests/monitoring_alertrules_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: monitoring PrometheusRule
templates:
  - templates/monitoring/alertmanager-rules.yaml
release:
  name: lolday
  namespace: monitoring
tests:
  - it: contains LoldayBackendErrorRateElevated rule
    asserts:
      - matchRegex:
          path: spec.groups[*].rules[*].alert
          pattern: "LoldayBackendErrorRateElevated"

  - it: contains GpuSignalFailSafeStuck rule
    asserts:
      - matchRegex:
          path: spec.groups[*].rules[*].alert
          pattern: "GpuSignalFailSafeStuck"

  - it: every critical alert has runbook_url annotation
    asserts:
      - matchSnapshot:
          path: spec.groups[*].rules[?(@.labels.severity=='critical')].annotations.runbook_url
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/monitoring_alertrules_test.yaml' .
git add charts/lolday/tests/monitoring_alertrules_test.yaml
git commit -m "test(charts): helm-unittest monitoring alertrules — critical alerts have runbooks (D1.9)"
```

---

### Task 30: helm-unittest — `alertmanagerconfig_test.yaml` (D1.9 — 6 of 7)

**Files:**

- Create: `charts/lolday/tests/alertmanagerconfig_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: AlertmanagerConfig routes
templates:
  - templates/monitoring/alertmanagerconfig.yaml
release:
  name: lolday
  namespace: monitoring
tests:
  - it: critical severity routes to Captain Hook with @here
    asserts:
      - matchRegex:
          path: spec.route.routes[?(@.matchers[0].value=='critical')].receiver
          pattern: "discord-critical"

  - it: warning severity routes to Spidey Warnings (no @here)
    asserts:
      - matchRegex:
          path: spec.route.routes[?(@.matchers[0].value=='warning')].receiver
          pattern: "discord-warning"

  - it: inhibit rules match the 2026-05-10 spec
    asserts:
      - lengthEqual:
          path: spec.inhibitRules
          count: 5
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/alertmanagerconfig_test.yaml' .
git add charts/lolday/tests/alertmanagerconfig_test.yaml
git commit -m "test(charts): helm-unittest AlertmanagerConfig severity routing + inhibit rules (D1.9)"
```

---

### Task 31: helm-unittest — `pss_test.yaml` (D1.9 — 7 of 7)

**Files:**

- Create: `charts/lolday/tests/pss_test.yaml`

- [ ] **Step 1: Author the suite**

```yaml
suite: Pod Security Standards labels
templates:
  - templates/namespaces.yaml
release:
  name: lolday
  namespace: lolday
tests:
  - it: lolday namespace enforces restricted PSS
    documentIndex: 0 # first namespace doc
    asserts:
      - equal:
          path: metadata.labels["pod-security.kubernetes.io/enforce"]
          value: restricted

  - it: lolday-jobs namespace enforces restricted PSS
    documentIndex: 1
    asserts:
      - equal:
          path: metadata.labels["pod-security.kubernetes.io/enforce"]
          value: restricted

  - it: lolday-builds namespace stays at privileged (hostPath required, PR #181)
    documentIndex: 2
    asserts:
      - equal:
          path: metadata.labels["pod-security.kubernetes.io/enforce"]
          value: privileged
```

- [ ] **Step 2: Run + Commit**

```bash
cd charts/lolday
helm unittest -f 'tests/pss_test.yaml' .
git add charts/lolday/tests/pss_test.yaml
git commit -m "test(charts): helm-unittest PSS labels per namespace (D1.9 complete)"
```

---

### Task 32: kubeconform + kyverno-cli in `lint.yml` (D1.10)

**Files:**

- Modify: `.github/workflows/lint.yml`

- [ ] **Step 1: Append two steps after the existing `pre-commit` job step**

```yaml
- name: Install kubeconform
  run: |
    curl -sSL https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz \
      | tar -xz -C /usr/local/bin kubeconform

- name: Render chart and kubeconform-validate
  run: |
    cd charts/lolday
    helm dep update
    helm template lolday . -f values-test.yaml --include-crds > /tmp/rendered.yaml
    kubeconform -strict -summary -schema-location default /tmp/rendered.yaml

- name: Install kyverno-cli
  run: |
    curl -sSL https://github.com/kyverno/kyverno/releases/download/v1.13.0/kyverno-cli_v1.13.0_linux_x86_64.tar.gz \
      | tar -xz -C /usr/local/bin kyverno

- name: kyverno-cli validate cluster policies
  run: |
    kyverno apply charts/lolday/templates/kyverno/ \
      --resource <(helm template lolday charts/lolday -f charts/lolday/values-test.yaml --include-crds)
```

- [ ] **Step 2: Smoke locally**

```bash
helm dep update charts/lolday
helm template lolday charts/lolday -f charts/lolday/values-test.yaml --include-crds | kubeconform -strict -summary -schema-location default -
```

Expected: every rendered resource validates.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/lint.yml
git commit -m "feat(ci): kubeconform + kyverno-cli validate chart renders (D1.10)"
```

---

### Task 33: `backend-fast.yml` workflow (D1.6 — 1 of 3)

**Files:**

- Create: `.github/workflows/backend-fast.yml`

- [ ] **Step 1: Author the workflow**

```yaml
name: backend-fast
on:
  pull_request:
    paths:
      - "backend/**"
      - ".github/workflows/backend-fast.yml"
  push:
    branches: [main]
    paths:
      - "backend/**"

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  pytest:
    name: pytest
    runs-on: ubuntu-24.04
    timeout-minutes: 8
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: ./.github/actions/setup-uv
        with:
          project-dir: backend
      - name: Run fast tier (unit + integration + contract, no heavy)
        working-directory: backend
        run: |
          uv run pytest -m 'not heavy' \
            --cov=app --cov-report=xml --cov-report=term \
            --junitxml=junit.xml \
            -v --tb=short
      - name: Upload coverage
        if: success() && github.event.pull_request.head.repo.fork == false
        uses: codecov/codecov-action@e28ff129e5465c2c0dcc6f003fc735cb6ae0c673 # v4.5.0
        with:
          files: backend/coverage.xml
          flags: backend
      - name: Upload JUnit
        if: always()
        uses: actions/upload-artifact@834a144ee995460fba8ed112a2fc961b36a5ec5a # v4.3.6
        with:
          name: junit-backend-fast-${{ github.run_id }}
          path: backend/junit.xml
```

- [ ] **Step 2: Local smoke**

```bash
cd backend
uv run pytest -m 'not heavy' -q
```

Expected: all non-heavy tests pass.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/backend-fast.yml
git commit -m "feat(ci): backend-fast.yml — PR fast tier (unit + integration + contract, no heavy)"
```

---

### Task 34: `backend-slow.yml` workflow (D1.6 — 2 of 3)

**Files:**

- Create: `.github/workflows/backend-slow.yml`

- [ ] **Step 1: Author the workflow**

```yaml
name: backend-slow
on:
  push:
    branches: [main]
    paths:
      - "backend/**"
  schedule:
    - cron: "0 19 * * *" # 03:00 Asia/Taipei
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  pytest-heavy:
    name: pytest heavy
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    services:
      docker:
        image: docker:24-dind
        options: --privileged
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: ./.github/actions/setup-uv
        with:
          project-dir: backend
      - name: Run heavy tier (testcontainers)
        working-directory: backend
        run: |
          uv run pytest -m heavy \
            --junitxml=junit-heavy.xml \
            -v --tb=short
      - name: Upload JUnit
        if: always()
        uses: actions/upload-artifact@834a144ee995460fba8ed112a2fc961b36a5ec5a # v4.3.6
        with:
          name: junit-backend-slow-${{ github.run_id }}
          path: backend/junit-heavy.xml
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/backend-slow.yml
git commit -m "feat(ci): backend-slow.yml — main + nightly heavy tier (testcontainers)"
```

---

### Task 35: Delete `backend.yml` (D1.6 — 3 of 3)

**Files:**

- Delete: `.github/workflows/backend.yml`

- [ ] **Step 1: Verify the new workflows cover everything `backend.yml` did**

The old `backend.yml` ran `pytest --cov=app`. `backend-fast.yml` does the
same for non-heavy tests; `backend-slow.yml` covers the heavy tier.
Together they replace it.

- [ ] **Step 2: Delete + commit**

```bash
git rm .github/workflows/backend.yml
git commit -m "ci: delete backend.yml (replaced by backend-fast + backend-slow)"
```

---

### Task 36: `chart-e2e.yml` workflow (D1.11)

**Files:**

- Create: `.github/workflows/chart-e2e.yml`

- [ ] **Step 1: Author the workflow**

```yaml
name: chart-e2e
on:
  push:
    branches: [main]
    paths:
      - "charts/**"
  schedule:
    - cron: "0 20 * * *" # 04:00 Asia/Taipei
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false

jobs:
  k3d-helm-smoke:
    name: k3d + helm install smoke
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Install k3d
        run: |
          curl -sSL https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | TAG=v5.7.5 bash

      - name: Spin up k3d cluster
        run: |
          k3d cluster create lolday-test --wait --timeout 5m
          kubectl cluster-info

      - name: helm dep update
        run: helm dep update charts/lolday

      - name: helm install lolday
        run: |
          helm install lolday charts/lolday \
            -n lolday --create-namespace \
            -f charts/lolday/values-test.yaml \
            --wait --timeout 10m

      - name: Smoke probes
        run: |
          kubectl -n lolday rollout status deploy/backend --timeout 5m
          kubectl -n lolday get pods
          # curl /healthz once port-forward is set up

      - name: helm upgrade to same version (idempotency)
        run: |
          helm upgrade lolday charts/lolday \
            -n lolday \
            -f charts/lolday/values-test.yaml \
            --wait --timeout 5m

      - name: Teardown
        if: always()
        run: k3d cluster delete lolday-test
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/chart-e2e.yml
git commit -m "feat(ci): chart-e2e.yml — k3d ephemeral + helm install/upgrade smoke (D1.11)"
```

---

### Task 37: `dispatch.yml` paths-filter (D1.12)

**Files:**

- Create: `.github/workflows/dispatch.yml`

- [ ] **Step 1: Author the workflow**

```yaml
name: dispatch
on:
  pull_request: {}
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  filter:
    runs-on: ubuntu-24.04
    outputs:
      backend: ${{ steps.filter.outputs.backend }}
      frontend: ${{ steps.filter.outputs.frontend }}
      charts: ${{ steps.filter.outputs.charts }}
      routers_jobs: ${{ steps.filter.outputs.routers_jobs }}
      mlflow_client: ${{ steps.filter.outputs.mlflow_client }}
      migrations: ${{ steps.filter.outputs.migrations }}
      networkpolicy: ${{ steps.filter.outputs.networkpolicy }}
      kyverno: ${{ steps.filter.outputs.kyverno }}
      monitoring: ${{ steps.filter.outputs.monitoring }}
      schema_gen: ${{ steps.filter.outputs.schema_gen }}
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - uses: dorny/paths-filter@de90cc6fb38fc0963ad72b210f1f284cd68cea36 # v3.0.2
        id: filter
        with:
          filters: |
            backend:
              - 'backend/**'
            frontend:
              - 'frontend/**'
            charts:
              - 'charts/**'
            routers_jobs:
              - 'backend/app/routers/jobs.py'
            mlflow_client:
              - 'backend/app/services/mlflow_client.py'
            migrations:
              - 'backend/migrations/**'
            networkpolicy:
              - 'charts/lolday/templates/networkpolicy/**'
            kyverno:
              - 'charts/lolday/templates/kyverno/**'
            monitoring:
              - 'charts/lolday/templates/monitoring/**'
            schema_gen:
              - 'frontend/src/api/schema.gen.ts'
```

(Downstream workflows can read these outputs via `needs.dispatch.outputs.<key>`
and gate jobs with `if:` conditions in subsequent phases.)

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/dispatch.yml
git commit -m "feat(ci): dispatch.yml with dorny/paths-filter for selective triggering (D1.12)"
```

---

### Task 38: `flaky-tracker.yml` weekly cron (D1.13)

**Files:**

- Create: `.github/workflows/flaky-tracker.yml`
- Create: `scripts/lib/flaky_aggregate.py`

- [ ] **Step 1: Author the aggregator script**

```python
"""Aggregate the last 7 days of JUnit XML artifacts; emit a per-test
failure-rate report. Tests with rate > 1 % trigger an auto-issue with
the 'flaky' label."""

from __future__ import annotations

import collections
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GH_REPO = "bolin8017/lolday"
THRESHOLD = 0.01


def parse_runs(artifact_dir: Path) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"total": 0, "fail": 0}
    )
    for xml in artifact_dir.rglob("junit*.xml"):
        tree = ET.parse(xml)
        for case in tree.iterfind(".//testcase"):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            stats[name]["total"] += 1
            if case.find("failure") is not None or case.find("error") is not None:
                stats[name]["fail"] += 1
    return stats


def main():
    artifact_dir = Path(sys.argv[1])
    stats = parse_runs(artifact_dir)
    flaky = [
        (name, s["fail"], s["total"], s["fail"] / s["total"])
        for name, s in stats.items()
        if s["total"] >= 10 and s["fail"] / s["total"] > THRESHOLD
    ]
    flaky.sort(key=lambda x: -x[3])
    for name, fail, total, rate in flaky:
        title = f"Flaky test: {name} ({rate:.1%} over {total} runs)"
        subprocess.run(
            [
                "gh", "issue", "create",
                "-R", GH_REPO,
                "-t", title,
                "-l", "flaky",
                "-b",
                f"`{name}` failed {fail}/{total} times in the last 7 days (failure rate {rate:.1%}).\n"
                f"Per `.claude/rules/testing.md`: 14d fix SLO, 21d delete SLO.",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Author the workflow**

```yaml
name: flaky-tracker
on:
  schedule:
    - cron: "0 6 * * 1" # 14:00 Asia/Taipei every Monday
  workflow_dispatch:

permissions:
  contents: read
  issues: write
  actions: read

jobs:
  aggregate:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Download last 7 days of JUnit artifacts
        uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea # v7.0.1
        with:
          script: |
            const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
            const { data: { artifacts } } = await github.rest.actions.listArtifactsForRepo({
              owner: 'bolin8017',
              repo: 'lolday',
              per_page: 100,
            });
            const recent = artifacts.filter(a => a.name.startsWith('junit-') && a.created_at > since);
            const fs = require('fs');
            fs.mkdirSync('artifacts', { recursive: true });
            for (const art of recent) {
              const { data } = await github.rest.actions.downloadArtifact({
                owner: 'bolin8017',
                repo: 'lolday',
                artifact_id: art.id,
                archive_format: 'zip',
              });
              fs.writeFileSync(`artifacts/${art.id}.zip`, Buffer.from(data));
            }
            // unzip each
            const { execSync } = require('child_process');
            for (const zip of fs.readdirSync('artifacts').filter(f => f.endsWith('.zip'))) {
              execSync(`unzip -q artifacts/${zip} -d artifacts/${zip.replace('.zip', '')}`);
            }

      - uses: actions/setup-python@0b93645e9fea7318ecaed2b359559ac225c90a2b # v5.3.0
        with:
          python-version: "3.12"

      - name: Run aggregator
        env:
          GH_TOKEN: ${{ github.token }}
        run: python scripts/lib/flaky_aggregate.py artifacts/
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/flaky-tracker.yml scripts/lib/flaky_aggregate.py
git commit -m "feat(ci): flaky-tracker.yml weekly cron — auto-issues for > 1% failure-rate tests (D1.13)"
```

---

### Task 39: Update branch-protection required_status_checks to new names

**Files:** (none in repo; GitHub Settings via `gh api`)

- [ ] **Step 1: Re-run the `gh api` PUT with new workflow names**

```bash
gh api -X PUT repos/bolin8017/lolday/branches/main/protection/required_status_checks \
  -F strict=true \
  -F 'contexts[]=lint / pre-commit' \
  -F 'contexts[]=backend-fast / pytest' \
  -F 'contexts[]=frontend / unit' \
  -F 'contexts[]=helm / lint-template' \
  -F 'contexts[]=images / build-image (backend)' \
  -F 'contexts[]=images / build-image (frontend)' \
  -F 'contexts[]=helpers / build-helper (build-helper)' \
  -F 'contexts[]=helpers / build-helper (job-helper)' \
  -F 'contexts[]=gitleaks / gitleaks-scan'
```

- [ ] **Step 2: Verify**

```bash
gh api repos/bolin8017/lolday/branches/main/protection/required_status_checks | jq '.contexts'
```

Expected: the nine names above.

- [ ] **Step 3: Update `docs/conventions.md` §10.6 step 2 to the new list, in a follow-up doc PR (not in this branch).**

---

### Task 40: Phase 1 exit verification

**Files:** (none modified — verification only)

- [ ] **Step 1: Full backend fast tier green**

```bash
cd backend
uv run pytest -m 'not heavy' -q
```

Expected: ≥ 96 + new tests pass; wall clock ≤ 4 min.

- [ ] **Step 2: Heavy tier green (locally — needs Docker)**

```bash
cd backend
uv run pytest -m heavy -v --tb=short
```

Expected: all 4 heavy test files pass; wall clock ≤ 15 min.

- [ ] **Step 3: helm-unittest all 6 suites green**

```bash
cd charts/lolday
helm dep update
helm unittest -f 'tests/*_test.yaml' .
```

Expected: 6 suites, all tests passed.

- [ ] **Step 4: Push branch and open PR**

```bash
git push -u origin docs/test-architecture-phase-1
gh pr create --title "feat(test-architecture): Phase 1 — Foundation, Critical Path, Helm Baseline" \
  --body "$(cat <<'EOF'
## Summary
- Reorgs backend/tests/ into unit/integration/contract/heavy subdirs.
- Splits the 850-line conftest.py into root + 3 layer-specific conftests.
- Refactors MlflowClient onto FastAPI lifespan injection (R2).
- Adds contract tier (schemathesis × 3 + MLflow respx + Volcano kubeconform).
- Adds heavy tier (testcontainers Postgres / MLflow / kubernetes-fake-client).
- Adds hypothesis state-machine + enum-totality invariants.
- Adds 6 helm-unittest suites covering backend Deployment, NP, Kyverno, monitoring, AlertmanagerConfig, PSS.
- Splits backend.yml into backend-fast.yml (PR) + backend-slow.yml (main / nightly).
- Adds chart-e2e.yml (k3d + helm install/upgrade smoke).
- Adds dispatch.yml (paths-filter for selective triggers).
- Adds flaky-tracker.yml (weekly cron, auto-issues on > 1 % failure rate).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md
Plan: docs/superpowers/plans/2026-05-15-test-architecture-phase-1.md

## Test plan

- [x] backend-fast.yml green (~96+ tests)
- [x] backend-slow.yml green locally (heavy tier; testcontainers)
- [x] helm.yml green (6 helm-unittest suites)
- [x] chart-e2e.yml green (k3d smoke)
- [x] lint.yml green (kubeconform + kyverno-cli validate)
- [x] flaky-tracker.yml dry-run green

Risk reduction (per spec §10 Phase 1):
- Cat 1 silent breakage: 30 % → 60 %
- Cat 3 prod-only bug: 10 % → 70 %
- Cat 5 infra regression: 0 % → 60 %

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: After merge, re-run Task 39 to update required_status_checks for the new workflow names.**

---

## Self-Review Coverage

| Spec deliverable                 | Task          | Status |
| -------------------------------- | ------------- | ------ |
| D0.4 .claude/rules/testing.md    | Task 1        | ✓      |
| D1.1 directory reorg             | Tasks 4 + 5   | ✓      |
| D1.2 conftest split (R1)         | Tasks 6–10    | ✓      |
| D1.3 dev deps                    | Task 2        | ✓      |
| D1.4 pyproject addopts + markers | Task 3        | ✓      |
| D1.5 MlflowClient lifespan (R2)  | Tasks 11–13   | ✓      |
| D1.6 backend.yml split           | Tasks 33–35   | ✓      |
| D1.7 contract tier (5 files)     | Tasks 14–18   | ✓      |
| D1.8 heavy tier (4 files)        | Tasks 19–22   | ✓      |
| D1.9 helm-unittest (6 suites)    | Tasks 25–31   | ✓      |
| D1.10 kubeconform + kyverno-cli  | Task 32       | ✓      |
| D1.11 chart-e2e.yml              | Task 36       | ✓      |
| D1.12 dispatch.yml               | Task 37       | ✓      |
| D1.13 flaky-tracker.yml          | Task 38       | ✓      |
| D1.14 hypothesis invariants (2)  | Tasks 23 + 24 | ✓      |
| Exit verification                | Task 40       | ✓      |

## Out-of-scope (handled by separate plans or PRs)

- Phase 0 D0.2 / D0.3 / D0.5 — small operator-manual PRs listed under
  Prerequisites above; can land in parallel.
- Phase 2 (R3 routers/jobs.py extract, R4 multi-persona, frontend MSW +
  visual + contract, security contract tests, frontend-slow.yml) — own
  plan.
- Phase 3 (frontend full E2E, multi-persona, i18n drift, a11y, R5
  schema.gen.ts split) — own plan.
- Phase 4 (bats, R6 scripts, mutation.yml, test-telemetry.yml) — own
  plan.
- Phase 5 (chaos / perf / fuzzing / 24h leak) — own plan, only on
  demonstrated need.
