# Frontend-slow live-stack k8s/MLflow stub layer — Design Specification

> **Drafted 2026-05-17.** This spec closes the deferred "frontend-slow
> k8s/MLflow stub layer" item from
> `2026-05-15-test-architecture-redesign-design.md` §10 D2.9. The
> Playwright live-stack used by `.github/workflows/frontend-slow.yml`
> currently boots `uvicorn` + `vite dev` against a sqlite in-memory DB
> but lets the backend reach out to whatever Kubernetes API + MLflow
> endpoint the host happens to expose. The spec defines an in-process
> stub layer — shared with the pytest integration tier — that the
> backend installs at lifespan start when an opt-in flag is set, so
> Playwright specs that exercise `POST /api/v1/jobs`, the reconciler
> loop, or the FIFO scheduler get deterministic, network-free
> responses without standing up a real cluster.

## 1. Overview

The frontend-slow workflow ships in `.github/workflows/frontend-slow.yml`
and runs the Playwright suite via the `webServer` block in
`frontend/playwright.config.ts`. That block boots:

- `uvicorn app.main:app` with `AUTH_DEV_MODE=true`,
  `ENVIRONMENT=development`,
  `DATABASE_URL=sqlite+aiosqlite:///file::memory:?cache=shared&uri=true`,
  `DOCS_ENABLED=true`,
- `pnpm dev` (vite).

`AUTH_DEV_MODE` plus the `AUTH_DEV_PERSONAS` header set (Phase 2 R4)
gives Playwright a deterministic auth path. `globalSetup` POSTs
`/api/v1/dev/seed-fixtures` to insert a detector, version, two datasets,
a `QUEUED_BACKEND` job, and a registered model + model-version. So far,
so good.

**Then the backend tries to reach outside the process.** The reconciler
loop and FIFO scheduler are both default-enabled — `RECONCILER_ENABLED`
and `FIFO_RECONCILER_ENABLED` in `app/config.py`. On every tick:

- `_run_fifo_reconciler_forever` calls `app.services.k8s.core_v1()` →
  `kubernetes.client.CoreV1Api()` → `list_namespaced_pod()` on whatever
  cluster `~/.kube/config` points at (or fails to load the kubeconfig
  entirely on a CI runner).
- The reconciler tick calls Volcano CRDs through
  `volcano_v1alpha1().get_namespaced_custom_object(...)`.
- The seeded `QUEUED_BACKEND` job is HEAD of the FIFO queue, so on the
  first tick the scheduler tries to call
  `core_v1().create_namespaced_secret(...)` then
  `volcano_v1alpha1().create_namespaced_custom_object(...)` to dispatch
  it.

And any Playwright spec that submits a real job (`jobs/full-lifecycle`,
`job-train`, `baseline-train-eval-flow`) hits `POST /api/v1/jobs`, which
calls `app.state.mlflow.get_or_create_experiment(...)` → real MLflow
REST.

There is no real cluster and no real MLflow on the CI runner. There
**is** one on the operator's workstation when `pnpm playwright test`
runs locally. The pytest integration tier solved this with the autouse
`mock_k8s_batch` + `mock_mlflow` fixtures in
`backend/tests/integration/conftest.py`. The live-stack has no
equivalent. Result:

- Locally: every reconciler tick leaks real `Volcano vcjob` /
  `Secret` CRs into the operator's cluster (the same regression the
  pytest stub layer was built to prevent — see
  `backend/tests/integration/conftest.py:131-136`).
- In CI: `kubernetes.config.load_*` fails, the loops counter-bump
  `BACKEND_ERRORS{stage="fifo_scheduler_iteration"}` /
  `{stage="reconciler_iteration"}` every tick, and any job-submitting
  Playwright spec 500s because MLflow is unreachable.

The plan-of-record (spec §10 D2.9) was "playwright E2E against k3d",
but a k3d-in-CI cluster is ~5 minutes of cold-start time per run plus
new test infrastructure to maintain. The cheaper, mainstream-pattern
move — already validated by ~200 integration tests — is to **install
the existing pytest stubs into the backend lifespan, behind an opt-in
env flag**.

## 2. Authorization

User (2026-05-17 system-review iteration handoff): pick up the
frontend-slow k8s/MLflow stub layer as spec-lane work. Breaking changes
OK; mainstream patterns first; one PR per concern.

Additional constraints inherited from project-level rules:

- **Lolday is a deploy platform, not a development platform** — no
  detector-author-overriding UI knobs.
- **Production must reject the dev flag** — boot-time validator already
  rejects `AUTH_DEV_MODE=true` in `ENVIRONMENT=production`. The new
  flag will piggy-back on the same gate.
- **No live-cluster leaks** — the existing pytest stub layer was
  introduced _because_ an early integration run leaked 515 stale
  Pending Jobs onto server30. The live-stack must hit the same safety
  invariant.

## 3. Scope

### 3.1 In scope

1. **New module `backend/app/services/_stubs.py`** — extracts the
   in-memory K8s + MLflow stub classes (`_StubBatch`, `_StubCore`,
   `_StubVolcano`, `_StubMlflowClient`) currently defined inside
   `backend/tests/integration/conftest.py`. The classes become
   importable from non-test code.
2. **Settings flag `SPEC_LANE_STUBS`** in `backend/app/config.py`:
   - Default `False`.
   - Refused in production via `Settings.validate_sso_config` (mirrors
     the existing `AUTH_DEV_MODE` rejection in production).
3. **Lifespan wiring in `backend/app/main.py`** — when
   `settings.SPEC_LANE_STUBS=true`, install the stubs **before**
   `app.state.mlflow` is constructed and before the reconciler / FIFO
   scheduler tasks are started:
   - Monkey-patch `app.services.k8s.batch_v1` / `core_v1` /
     `volcano_v1alpha1` to return module-level singleton stubs.
   - Monkey-patch every from-import rebound name in the 9 caller
     modules listed in
     `backend/tests/integration/conftest.py:189-208` (same list,
     verbatim — that file is the truth-source for which modules need
     re-binding).
   - Override `app.state.mlflow` with `_StubMlflowClient()` instead of
     constructing a real `MlflowClient`.
   - Override `app.services.k8s.load_config` with the same
     `_safe_load_config` shim used in `_mock_k8s_load_config` so the
     kubernetes library never tries to read `~/.kube/config`.
4. **Test-code refactor** —
   `backend/tests/integration/conftest.py` imports stub classes from
   `app.services._stubs` instead of redefining them. The autouse
   fixtures still own the `monkeypatch` plumbing (per-test isolation).
5. **`frontend/playwright.config.ts` webServer env** — set
   `SPEC_LANE_STUBS=true` so `pnpm playwright test` (local + CI) boots
   uvicorn with the stubs installed.
6. **`.github/workflows/frontend-slow.yml`** — set the same env on the
   Playwright step. (`playwright.config.ts` propagates env to the
   spawned uvicorn, but the workflow step also needs the flag if any
   future workflow boots uvicorn outside `playwright.config.ts`.)
7. **A new heavy-stage integration test** —
   `backend/tests/integration/services/test_stubs_module.py` exercises
   the extracted classes directly (one class can ship a regression if
   the test re-import path doesn't catch it).
8. **Docs** — `docs/architecture.md` §10 #34 (new entry) marks the
   stub-layer item as resolved; `.claude/rules/frontend.md` gets a
   one-line pointer; `.claude/rules/testing.md` gets a §13 entry that
   names the shared module.

### 3.2 Out of scope

- A real k3d cluster in CI. The chart-e2e workflow (`chart-e2e.yml`)
  already covers k3d for chart shape; replicating it inside
  frontend-slow doubles cost for marginal coverage above the in-process
  stubs.
- The `KubernetesClient` Protocol refactor that spec §9.4 proposes —
  the protocol is the right long-term shape, but it is its own
  multi-PR effort. The stub-layer wiring works on top of the existing
  module-level functions and does not block the protocol later. (The
  protocol becomes the natural next step once _any_ live-stack caller
  needs richer fake behaviour than the current stubs provide; today
  the stubs are sufficient because Playwright specs only need the
  K8s call to _succeed_, not to simulate scheduling decisions.)
- A heavy-tier `kubernetes-fake-client` (`heavy/k8s_fake/`) — spec
  §5.1 mentions this directory and a fictional `kubernetes-fake-client`
  package; auto-memory
  [[project_kubernetes_fake_client_does_not_exist]] notes that the
  package does not exist on PyPI and the integration-tier
  `mock_k8s_batch` is the actual in-tree replacement. This spec
  reuses the in-tree stubs and does not introduce a heavy-tier
  k8s fake.
- New Playwright specs that exercise the dispatch path. Adding more
  specs is a follow-up; this spec only fixes the foundation.

## 4. Background — what was on the air

### 4.1 The pytest stub layer (already shipped)

`backend/tests/integration/conftest.py:71-208` defines three autouse
fixtures:

- `_mock_k8s_load_config` — replaces
  `app.services.k8s.load_config` with a variant that swallows
  `kubernetes.config.config_exception.ConfigException`.
- `mock_k8s_batch` — installs `_StubBatch` / `_StubCore` /
  `_StubVolcano` instances and patches both:
  - `app.services.k8s.batch_v1` / `core_v1` / `volcano_v1alpha1`
    (source bindings), and
  - the same names in 9 caller modules that did
    `from app.services.k8s import …`:
    - `app.services.harbor_init`
    - `app.services.cluster_status`
    - `app.services.job_dispatch`
    - `app.routers.detectors`
    - `app.routers.jobs`
    - `app.reconciler.builds`
    - `app.reconciler.jobs`
    - `app.reconciler.log_capture`
    - `app.reconciler.orphans`
- `mock_mlflow` — installs `_Stub` as `app.dependency_overrides[get_mlflow]`
  for all routes using the new DI path.

The stubs implement the union of every K8s + MLflow call any of those
modules makes — enough for the ~200 integration tests to pass without
network egress. They are tested _behaviorally_ every CI run because
the integration tier exercises them as a side effect of testing the
backend.

### 4.2 The frontend-slow live-stack today

`frontend/playwright.config.ts` ships a `webServer` block that boots
`uvicorn app.main:app`. The block sets `AUTH_DEV_MODE`, the dev-mode
auth bypass, and a sqlite in-memory DB. It does **not** set anything
that disables the reconciler / FIFO scheduler.

`backend/app/main.py:181-197` starts both background tasks by default.
The seeded `QUEUED_BACKEND` job (from `dev_seed.py`) is HEAD of the
FIFO queue, so on tick #1 the scheduler tries to dispatch it through
the (un-stubbed) K8s API. On a CI runner with no kubeconfig, this
fails _at K8s client init time_; `BACKEND_ERRORS{stage=
"fifo_scheduler_iteration"}` bumps every `FIFO_RECONCILER_PERIOD_SECONDS`
(default 30s) for the rest of the test run.

Today the Playwright suite "passes" because no spec asserts on those
metrics, and the specs that hit `POST /api/v1/jobs`
(`jobs/full-lifecycle`) currently 500 silently — the spec asserts on
the response body being `id`-bearing, so it fails, but is masked by
the suite being "informational" and not blocking PRs. The frontend-slow
gate has been green-ish in practice because the destructive specs are
either skipped via env-gate or pass through routes that don't reach
MLflow.

### 4.3 Why this matters now

Two pressures push the fix to the front of the queue:

1. **The Phase 3 D3.3 critical-flow E2E** (`jobs/full-lifecycle.spec.ts`)
   exists and asserts `submitResp.status() === 202`. Without the stub
   layer, that spec is silently broken — the failure either points at a
   k3d gap (D2.9 plan-of-record) or at a dev-mode MLflow leak (the
   safety problem). Either way, the spec is not actually buying the
   "POST /api/v1/jobs round-trip green" gate that Phase 3 promised.
2. **The cluster-leak risk recurs on every operator `pnpm playwright
test`.** Today the operator's kubeconfig points at server30; the
   live-stack will create real Volcano vcjobs / Secrets every time the
   suite runs locally. This is the exact regression
   `backend/tests/integration/conftest.py:131-136` notes as the reason
   the stub layer was added in the first place.

## 5. Architecture decisions

### 5.1 Why install pytest stubs at lifespan, not build a separate fake-service

Three options were considered. The chosen one is (a).

| #   | Approach                                                                                                       | Pros                                                                      | Cons                                                                                           |
| --- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| (a) | **Extract pytest stubs into `app.services._stubs`; install at lifespan when `SPEC_LANE_STUBS=true`.** ◀ chosen | Single source of truth; battle-tested by integration tier; zero new deps. | Module-level rebinding ceremony (9 modules) lives in production code path (gated by flag).     |
| (b) | Build a parallel fake-service for live-stack only (e.g. `app.services._spec_fake.py`).                         | Cleaner separation of test vs. live code.                                 | Two stubs to maintain → spec drift (the exact risk Phase 2 plan §line 1323 already flagged).   |
| (c) | k3d-in-CI per `frontend-slow.yml`; install chart; backend talks to a real K8s + MLflow.                        | Closest to prod.                                                          | ~5 min cluster boot per run, new infra to maintain, doesn't help local `pnpm playwright test`. |

**(a) wins** because the stubs already exist, are kept honest by the
integration tier, and the only "new" production code is the import +
the lifespan wiring (both gated on a `False`-by-default flag that
production refuses to even accept).

### 5.2 Why a module-level shared singleton, not per-request

The pytest fixtures recreate the stubs per-test (function-scope autouse)
for isolation. The live-stack does not need per-request isolation — it
needs per-process state, mirroring the real K8s API server, so a
sequence of calls (create vcjob → list pods → read pod log) sees the
same state.

The lifespan installs **module-level singletons** stored on `app.state`:

- `app.state.stub_batch`
- `app.state.stub_core`
- `app.state.stub_volcano`
- `app.state.stub_mlflow` (becomes `app.state.mlflow` so existing
  `get_mlflow()` dependency works unchanged).

`app.services.k8s.batch_v1` / `core_v1` / `volcano_v1alpha1` are
rebound to lambdas that return the singletons. The same rebinding is
applied to the 9 from-import caller modules. The rebinding happens
**before** any background task is created, so the reconciler /
scheduler never sees the un-stubbed bindings.

### 5.3 Why piggy-back on `Settings.validate_sso_config` for prod refusal

The existing validator already rejects boot when
`ENVIRONMENT == "production" AND AUTH_DEV_MODE == True`. Adding the
new flag to the same validator means:

- Single place to read the safety story.
- One CrashLoopBackOff is enough — no second silent-failure path.
- Consistent with the existing `_bootstrap_dev_schema_if_empty()`
  gate that uses `AUTH_DEV_MODE` to gate the create_all bootstrap.

### 5.4 Why singletons live on `app.state`, not module globals

`app.state` is FastAPI's idiomatic per-app store. Tests that ASGI-mount
the app directly can override it cleanly; module globals can't.
`app.state.mlflow` already follows this pattern (set at lifespan,
read by `get_mlflow()` dependency). The new K8s stubs sit next to it.

### 5.5 The fake `load_config` shim

`app.services.k8s.load_config` is `@lru_cache(maxsize=1)`-decorated and
called the first time any of `batch_v1()` / `core_v1()` /
`volcano_v1alpha1()` runs. If the kubernetes config loader can't find
a kubeconfig (CI runner case), it raises `ConfigException`. The pytest
fixture replaces the function with a try/except wrapper; the live-stack
needs the same. The shared module exports `safe_load_config()`.

When the live-stack runs on the operator's workstation (kubeconfig
exists), `safe_load_config` _does_ load the real config. That's fine —
no subsequent K8s call ever reaches the real cluster because the
stubs intercept everything at the `core_v1()` / `batch_v1()` /
`volcano_v1alpha1()` layer. The kubeconfig load is a no-op from a
safety perspective.

## 6. Detailed design

### 6.1 New module `app.services._stubs`

```python
# backend/app/services/_stubs.py
"""Shared in-process stubs for K8s + MLflow.

Used by:
- backend/tests/integration/conftest.py (autouse, per-test instances)
- backend/app/main.py lifespan (when SPEC_LANE_STUBS=true, singleton)

DO NOT IMPORT FROM HERE IN PRODUCTION CODE PATHS UNLESS THE CALLER
IS GATED ON settings.SPEC_LANE_STUBS — production refuses boot when
that flag is true.
"""
from __future__ import annotations

import contextlib
import uuid


def safe_load_config() -> None:
    """Try in-cluster, then user-local; swallow if neither exists."""
    from kubernetes import config as _kube_config
    from kubernetes.config.config_exception import ConfigException

    try:
        _kube_config.load_incluster_config()
    except ConfigException:
        with contextlib.suppress(ConfigException):
            _kube_config.load_kube_config()


class StubBatch: ...
class StubCore: ...
class StubVolcano: ...
class StubMlflowClient: ...
```

Each `Stub*` class is the verbatim body lifted from
`backend/tests/integration/conftest.py` (no behaviour changes; the
fixtures and the live-stack share the same body).

### 6.2 Lifespan wiring

```python
# backend/app/main.py — inside lifespan, BEFORE app.state.mlflow assignment
if settings.SPEC_LANE_STUBS:
    from app.services import _stubs

    _install_spec_lane_stubs(app)
    # ...everything below now uses the stubs.
```

`_install_spec_lane_stubs(app)`:

1. Replaces `app.services.k8s.load_config` with
   `_stubs.safe_load_config`.
2. Builds the three singletons; assigns to `app.state.stub_batch`
   etc.; rebinds `app.services.k8s.batch_v1` / `core_v1` /
   `volcano_v1alpha1` to return the singletons.
3. Rebinds the same names in the 9 caller modules. Module list is
   the single source-of-truth in `_stubs.CALLER_MODULE_REBIND_TARGETS`
   (list of `(module_path, name)` tuples). Pytest conftest imports
   the same constant.
4. Constructs `_stubs.StubMlflowClient()` and assigns to
   `app.state.mlflow`; skips the real `MlflowClient.from_settings`
   call.

The function is idempotent: re-installing in the same process is a
no-op (lifespan only runs once per process, but tests that mount the
ASGI app directly may call it multiple times).

### 6.3 Settings change

```python
# backend/app/config.py
class Settings(BaseSettings):
    # ...
    SPEC_LANE_STUBS: bool = False
    """Install in-process K8s + MLflow stubs at lifespan start. Refused
    in production. Used by frontend-slow Playwright suite."""

    @model_validator(mode="after")
    def validate_sso_config(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if self.AUTH_DEV_MODE:
                raise ValueError(...)  # existing
            if self.SPEC_LANE_STUBS:
                raise ValueError(
                    "SPEC_LANE_STUBS=true is forbidden when ENVIRONMENT=production"
                )
        # ...
```

### 6.4 Test refactor

`backend/tests/integration/conftest.py:71-208` becomes thin:

```python
@pytest.fixture(autouse=True)
def _mock_k8s_load_config(monkeypatch):
    from app.services._stubs import safe_load_config
    monkeypatch.setattr("app.services.k8s.load_config", safe_load_config)


@pytest.fixture(autouse=True)
def mock_k8s_batch(monkeypatch):
    from app.services._stubs import (
        CALLER_MODULE_REBIND_TARGETS, StubBatch, StubCore, StubVolcano
    )
    batch, core, volcano = StubBatch(), StubCore(), StubVolcano()
    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: batch)
    monkeypatch.setattr("app.services.k8s.core_v1", lambda: core)
    monkeypatch.setattr("app.services.k8s.volcano_v1alpha1", lambda: volcano)
    for module_path, name in CALLER_MODULE_REBIND_TARGETS:
        target = {"batch_v1": batch, "core_v1": core, "volcano_v1alpha1": volcano}[name]
        monkeypatch.setattr(f"{module_path}.{name}", lambda t=target: t)


@pytest.fixture(autouse=True)
def mock_mlflow(request):
    from app.services._stubs import StubMlflowClient
    # ...existing no_mock_mlflow branch unchanged...
    from app.deps import get_mlflow
    from app.main import app as fastapi_app
    stub = StubMlflowClient()
    fastapi_app.dependency_overrides[get_mlflow] = lambda: stub
    yield stub
    fastapi_app.dependency_overrides.pop(get_mlflow, None)
```

The behaviour is unchanged — every existing integration test continues
to pass. The diff is **only** the import path.

### 6.5 Playwright + workflow changes

```ts
// frontend/playwright.config.ts (env block inside backend webServer)
env: {
  AUTH_DEV_MODE: "true",
  AUTH_DEV_EMAIL: "admin@dev.local",
  ENVIRONMENT: "development",
  DATABASE_URL: "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
  CF_ACCESS_TEAM_DOMAIN: "",
  CF_ACCESS_APP_AUD: "",
  DOCS_ENABLED: "true",
  SPEC_LANE_STUBS: "true",  // ◀ new
},
```

```yaml
# .github/workflows/frontend-slow.yml — env on the Run playwright step
env:
  E2E_BASE_URL: http://127.0.0.1:5173
  DOCS_ENABLED: "true"
  SPEC_LANE_STUBS: "true" # ◀ new
```

The workflow-level env is a belt-and-braces guard for the case where a
future step boots uvicorn outside `playwright.config.ts` (e.g. a
healthcheck warm-up). `playwright.config.ts` already passes env into
the spawned uvicorn via the `webServer.env` block, so the suite works
locally even without the workflow-level setting.

### 6.6 New direct-test for the shared module

`backend/tests/integration/services/test_stubs_module.py` —
a 6-test suite that exercises the module surface directly:

- `test_stub_batch_create_then_read_404` — verifies the
  `read_namespaced_job` raises `ApiException(status=404)` after delete.
- `test_stub_core_secret_patches_recorded` —
  verifies `patch_namespaced_secret` records the call (the
  `M-token-secret-owner` invariant).
- `test_stub_volcano_create_then_list` — verifies
  `list_namespaced_custom_object` returns the created items.
- `test_stub_volcano_get_returns_404_by_default` — preserves the
  current pytest behaviour where `get_namespaced_custom_object` is
  always 404 (reconciler tests rely on this).
- `test_stub_mlflow_get_or_create_experiment_increments_counter` —
  verifies the run-counter monotonicity for the live-stack case
  where the same experiment is created twice.
- `test_safe_load_config_swallows_config_exception` — verifies the
  config-loader shim absorbs the no-kubeconfig path.

These guard against future refactors of the shared module — once the
file is imported from production code, an accidental regression is
strictly more expensive than the current "test-only" version.

## 7. Failure modes

| Failure                                                                                                       | Detection                                                                                                                                                                   | Mitigation                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Operator boots production with `SPEC_LANE_STUBS=true` in `.env`                                               | `Settings.validate_sso_config` raises at lifespan; pod CrashLoopBackOff                                                                                                     | The validator covers this — no silent fallback. The CrashLoop is the alert.                                                                                 |
| A new caller module does `from app.services.k8s import core_v1` and isn't in `CALLER_MODULE_REBIND_TARGETS`   | `BACKEND_ERRORS{stage="fifo_scheduler_iteration"}` bumps on the un-stubbed path in live-stack; integration test fails the same way                                          | The single-source-of-truth list keeps the live-stack and pytest tier in lockstep. A new caller in the wrong list breaks tests _and_ live-stack identically. |
| Stub diverges from real K8s / MLflow REST shape (e.g. MLflow adds a new required arg)                         | Real-MLflow heavy tier (`backend/tests/heavy/mlflow/test_acl_real_multi_user.py`) catches it; respx contract tier (`backend/tests/contract/mlflow/`) catches the REST shape | Two layers of coverage already exist; stub-layer divergence is a known limitation flagged in spec §5.1 row (a).                                             |
| Lifespan installs stubs but a route bypasses the dependency and directly imports `MlflowClient.from_settings` | Code review; spec §11 maintenance note adds a `.claude/rules/backend.md` line saying "the only path to MlflowClient is `Depends(get_mlflow)`"                               | Pre-existing rule (backend.md says routes use `Depends(get_mlflow)`). The new stub layer reinforces it.                                                     |

## 8. Testing strategy

| Tier        | What it covers                                                                                              | Existing or new                                                       |
| ----------- | ----------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Unit        | `app.services._stubs` direct-call behaviour                                                                 | New: `backend/tests/integration/services/test_stubs_module.py` (§6.6) |
| Integration | ~200 existing tests under `backend/tests/integration/` continue to pass via the refactored autouse fixtures | Existing (no behaviour change)                                        |
| Contract    | `backend/tests/contract/mlflow/` (real MLflow REST shape via respx replay) — unchanged                      | Existing                                                              |
| Heavy       | `backend/tests/heavy/mlflow/test_acl_real_multi_user.py` — real MLflow container — unchanged                | Existing                                                              |
| E2E (slow)  | Playwright `jobs/full-lifecycle.spec.ts` now actually gets a 202 because MLflow is stubbed                  | Existing spec, finally works                                          |

Exit criteria for this PR:

- `cd backend && uv run pytest backend/tests/integration` — all green.
- `cd frontend && pnpm playwright test tests/e2e/jobs/full-lifecycle.spec.ts` — green (locally, on CI).
- `cd backend && uv run pytest backend/tests/integration/services/test_stubs_module.py` — 6 cases green.
- Smoke: boot uvicorn with `SPEC_LANE_STUBS=true`, POST a job, verify the response is 202, verify `kubectl get vcjob -A` on the operator's cluster shows no new resources.

## 9. Rollback

The flag is `False` by default. Rollback = `git revert` the implementation
PR, which restores `backend/tests/integration/conftest.py` to inline
stubs and removes the lifespan wiring. The flag stays in `Settings`
harmlessly until removed in a follow-up commit (or kept indefinitely;
the cost is one boolean field).

No data migration. No chart change.

## 10. Open questions / future work

1. **K3d-in-CI** — still on the long-term roadmap (spec §10 Phase 5
   optional). The trigger to escalate from this stub layer to a real
   k3d run is a Playwright spec that needs _true_ Volcano scheduling
   behaviour (e.g. a spec that asserts a job moves from `running` to
   `succeeded` based on pod-status sync). Today no spec asserts on
   that; when one is needed, that's the moment to file a separate
   plan for k3d-in-CI.
2. **`KubernetesClient` Protocol** — spec §9.4 long-term refactor.
   Becomes the natural next step if/when the stubs need richer
   behaviour (e.g. simulating preemption, or a vcjob-controller-style
   state machine). Not blocked by this spec; doesn't block this spec.
3. **Stub-vs-real drift alert** — could add a weekly cron that runs
   the contract tier against the latest MLflow image to surface
   upstream REST shape changes. Out of scope; the heavy tier already
   tests the real container on every `backend-slow.yml` run.
4. **Frontend MSW vs. backend stubs** — the frontend integration tier
   uses MSW (Phase 2 D2.6) to mock the _backend_ surface; this spec
   stubs what the backend talks to. Both serve different layers and
   don't overlap.

## 11. References

- Source spec: `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`
  §10 D2.9, §5.1, §5.3, §6.1, §9.4.
- Existing pytest stub: `backend/tests/integration/conftest.py:71-208`.
- Lifespan callsite: `backend/app/main.py:135-200`.
- Playwright webServer: `frontend/playwright.config.ts:31-61`.
- Frontend-slow workflow: `.github/workflows/frontend-slow.yml`.
- Auto-memory: `project_kubernetes_fake_client_does_not_exist.md`
  (why the spec doesn't introduce a heavy-tier `k8s_fake` package).
- Related convention: `.claude/rules/backend.md` §Auth design,
  §Startup fail-fast behaviour.
