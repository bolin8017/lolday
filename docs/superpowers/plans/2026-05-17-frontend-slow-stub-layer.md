# Frontend-slow live-stack k8s/MLflow stub layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the design in
`docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md` —
extract the in-process K8s + MLflow stubs out of
`backend/tests/integration/conftest.py` into a reusable
`app.services._stubs` module, wire them into the FastAPI lifespan
behind a `SPEC_LANE_STUBS` flag (refused in production), and turn the
flag on in `frontend/playwright.config.ts` and
`.github/workflows/frontend-slow.yml`. After this lands, the
frontend-slow live-stack runs deterministically without leaking real
Volcano CRs onto the operator's cluster and without crashing on CI
where no kubeconfig exists.

**Architecture:** One PR, six commits (one per task block). No spec
revision needed mid-flight — the design covers every decision; only
verification + the precise diff need to be added in this plan.

**Tech stack:** No new deps. Uses existing `kubernetes` library,
`pydantic-settings`, `pytest`, `playwright`. The stubs are pure Python
classes lifted verbatim from `backend/tests/integration/conftest.py`.

---

## Reference

**Source spec:** `docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md`.

**Predecessor docs:**

- `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`
  §10 D2.9 (the deferred item this plan closes).
- `docs/superpowers/plans/2026-05-16-test-architecture-phase-3.md`
  (D3.3 critical-flow E2E that needs the stub layer to actually work).

**Files touched (preview)**

New:

- `backend/app/services/_stubs.py` (Task 1)
- `backend/tests/integration/services/test_stubs_module.py` (Task 5)

Modified:

- `backend/app/config.py` (Task 2 — `SPEC_LANE_STUBS` flag + validator)
- `backend/app/main.py` (Task 3 — lifespan install)
- `backend/tests/integration/conftest.py` (Task 4 — import from shared module)
- `frontend/playwright.config.ts` (Task 6 — env)
- `.github/workflows/frontend-slow.yml` (Task 6 — env)
- `docs/architecture.md` §10 (Task 7 — closure note as a new numbered entry)
- `.claude/rules/testing.md` (Task 7 — one-line pointer)

Deleted:

- None.

---

## Prerequisites

- [x] **PR #198 merged** — Phase 3 D3.3 ships
      `jobs/full-lifecycle.spec.ts` and `dev_seed.py`. Verified by
      `git log --oneline -- frontend/tests/e2e/jobs/full-lifecycle.spec.ts`.
- [x] **PR #196 merged** — Phase 2 R4 ships `AUTH_DEV_PERSONAS`. The
      Playwright spec uses `loginAs(page, "admin")` which depends on
      this. Verified by `grep -n AUTH_DEV_PERSONAS backend/app/config.py`.
- [x] **`backend/tests/integration/conftest.py`** ships the three
      autouse fixtures intact (`_mock_k8s_load_config`, `mock_k8s_batch`,
      `mock_mlflow`). Verified by Read.
- [x] **No outstanding `SPEC_LANE_STUBS` references** anywhere in the
      tree (sanity check that the symbol is unused). Verify with
      `grep -rn SPEC_LANE_STUBS backend frontend .github docs`.

If any of the above is missing or red, stop and resolve before
starting.

---

## Lessons baked into this plan

Three previous-session lessons inform task ordering:

1. **Verify exact bindings before refactoring.** Phase 3 burned half a
   task on a wrong field name (`Detector.git_repo_url` vs `git_url`).
   Tasks 1 + 4 below quote the exact module + attribute names from
   `conftest.py:189-208` verbatim so the rebind list cannot drift.

2. **Run pytest with the _full_ integration tier after each refactor
   step.** A partial run hides cross-fixture interactions. The
   verification block in Task 4 explicitly runs
   `cd backend && uv run pytest tests/integration -x` not a subset.

3. **Lifespan code paths are only executed when uvicorn boots — not
   when tests ASGI-mount the app.** Task 3 adds an explicit
   `_install_spec_lane_stubs` function so tests can call it directly
   if they want to exercise the lifespan path; the function is
   idempotent.

---

## Tasks

### Task 1: Extract stubs into `app.services._stubs` (no test/code wire-up yet)

**Files:**

- Create: `backend/app/services/_stubs.py`

- [ ] **Step 1: Verify the source bindings have not drifted.**

```bash
grep -n "_StubBatch\|_StubCore\|_StubVolcano\|_Stub:" backend/tests/integration/conftest.py
grep -n "monkeypatch.setattr" backend/tests/integration/conftest.py | head -20
```

Confirm the class names and the rebind list match what the spec §4.1
records (the 9 caller modules).

- [ ] **Step 2: Create `backend/app/services/_stubs.py`.**

Copy the bodies of `_StubBatch` (conftest lines 75–100), `_StubCore`
(lines 105–128), `_StubVolcano` (lines 138–180), and `_Stub`
(lines 233–314) verbatim. Rename class names (drop the leading
underscore for module-level reuse → `StubBatch`, `StubCore`,
`StubVolcano`, `StubMlflowClient`). The leading-underscore on the
module name (`_stubs.py`) signals "internal; gated by
SPEC_LANE_STUBS".

Add at module top:

```python
"""Shared in-process stubs for K8s + MLflow.

Used by:
- backend/tests/integration/conftest.py (autouse, per-test instances)
- backend/app/main.py lifespan (when SPEC_LANE_STUBS=true, singletons)

Production refuses boot when SPEC_LANE_STUBS=true (see
Settings.validate_sso_config). Importing this module from production
code paths outside that flag is a bug — code review should flag it.
"""
```

Define the rebind-target constant:

```python
# Single source of truth for module-level rebinding.
# `pytest` autouse fixtures and the live-stack lifespan share this list.
# Add a new entry whenever a new caller module imports
# `from app.services.k8s import {batch_v1, core_v1, volcano_v1alpha1}`.
CALLER_MODULE_REBIND_TARGETS: list[tuple[str, str]] = [
    ("app.services.harbor_init", "core_v1"),
    ("app.services.cluster_status", "volcano_v1alpha1"),
    ("app.services.job_dispatch", "core_v1"),
    ("app.services.job_dispatch", "volcano_v1alpha1"),
    ("app.routers.detectors", "batch_v1"),
    ("app.routers.detectors", "core_v1"),
    ("app.routers.jobs", "batch_v1"),
    ("app.routers.jobs", "core_v1"),
    ("app.reconciler.builds", "batch_v1"),
    ("app.reconciler.builds", "core_v1"),
    ("app.reconciler.jobs", "core_v1"),
    ("app.reconciler.jobs", "volcano_v1alpha1"),
    ("app.reconciler.log_capture", "core_v1"),
    ("app.reconciler.orphans", "core_v1"),
    ("app.reconciler.orphans", "volcano_v1alpha1"),
]
```

Define `safe_load_config()` — copy the body of `_safe_load_config`
from conftest lines 48–56.

- [ ] **Step 3: Verify the module imports clean.**

```bash
cd backend && uv run python -c "from app.services import _stubs; print(_stubs.CALLER_MODULE_REBIND_TARGETS)"
```

Output should list the 15 (module, name) tuples without error.

- [ ] **Step 4: Run ruff + mypy on the new file.**

```bash
cd backend && uv run ruff check app/services/_stubs.py
cd backend && uv run mypy app/services/_stubs.py
```

Both clean.

**Verification:**

- File compiles.
- ruff + mypy clean.
- No callers yet — pytest behaviour unchanged.

### Task 2: Add `SPEC_LANE_STUBS` setting + production-refusal validator

**Files:**

- Modify: `backend/app/config.py`

- [ ] **Step 1: Add the new boolean field.**

Locate the `Settings` class. Add immediately after `AUTH_DEV_MODE`:

```python
SPEC_LANE_STUBS: bool = Field(
    default=False,
    description=(
        "Install in-process K8s + MLflow stubs at lifespan start. "
        "Used by the frontend-slow Playwright live-stack to avoid "
        "leaking Volcano CRs onto the operator's cluster. Refused "
        "in production."
    ),
)
```

- [ ] **Step 2: Extend `validate_sso_config` to refuse the flag in prod.**

Locate the model_validator. Add the new check next to the existing
`AUTH_DEV_MODE` rejection:

```python
if self.SPEC_LANE_STUBS and self.ENVIRONMENT == "production":
    raise ValueError(
        "SPEC_LANE_STUBS=true is forbidden when ENVIRONMENT=production "
        "(stubs would replace real K8s + MLflow calls in prod traffic)"
    )
```

- [ ] **Step 3: Verify the validator fires.**

```bash
cd backend && uv run python -c "
from app.config import Settings
try:
    Settings(ENVIRONMENT='production', SPEC_LANE_STUBS=True)
except ValueError as e:
    print('OK:', e)
"
```

Output: `OK: SPEC_LANE_STUBS=true is forbidden ...`

- [ ] **Step 4: Verify the default is `False` everywhere else.**

```bash
cd backend && uv run python -c "
from app.config import Settings
s = Settings()
assert s.SPEC_LANE_STUBS is False, s.SPEC_LANE_STUBS
print('OK')
"
```

**Verification:**

- ruff + mypy clean on `config.py`.
- Production refusal works.
- Default is `False` (existing tests unaffected).

### Task 3: Wire stubs into the FastAPI lifespan

**Files:**

- Modify: `backend/app/main.py`

- [ ] **Step 1: Define `_install_spec_lane_stubs(app)` at module scope.**

Place it directly above the `lifespan` function. Function body:

```python
def _install_spec_lane_stubs(app: FastAPI) -> None:
    """Install in-process K8s + MLflow stubs.

    Idempotent: re-binding a module attribute that already points to a
    stub is a no-op. Stores the singletons on app.state for test access
    and for the lifespan teardown path.
    """
    import importlib

    from app.services import _stubs, k8s as _k8s

    _k8s.load_config = _stubs.safe_load_config  # type: ignore[assignment]

    batch = _stubs.StubBatch()
    core = _stubs.StubCore()
    volcano = _stubs.StubVolcano()
    app.state.stub_batch = batch
    app.state.stub_core = core
    app.state.stub_volcano = volcano

    _k8s.batch_v1 = lambda: batch  # type: ignore[assignment]
    _k8s.core_v1 = lambda: core  # type: ignore[assignment]
    _k8s.volcano_v1alpha1 = lambda: volcano  # type: ignore[assignment]

    for module_path, name in _stubs.CALLER_MODULE_REBIND_TARGETS:
        module = importlib.import_module(module_path)
        target = {"batch_v1": batch, "core_v1": core, "volcano_v1alpha1": volcano}[name]
        setattr(module, name, (lambda t=target: t))

    app.state.mlflow = _stubs.StubMlflowClient()
```

- [ ] **Step 2: Call it inside `lifespan`, BEFORE `app.state.mlflow`
      assignment and BEFORE the reconciler / FIFO scheduler tasks start.**

Locate the line that constructs `app.state.mlflow`. Insert above:

```python
if settings.SPEC_LANE_STUBS:
    _install_spec_lane_stubs(app)
else:
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    app.state.mlflow = MlflowClient.from_settings(settings, app.state.http)
```

The `else` branch wraps the existing two lines (do not duplicate them
above the `if`). When stubs are installed, `app.state.http` is _not_
created — the `StubMlflowClient` does not need an httpx.AsyncClient.

- [ ] **Step 3: Verify the imports are right.**

```bash
cd backend && uv run ruff check app/main.py
cd backend && uv run mypy app/main.py
```

`# type: ignore[assignment]` is required on the four `_k8s.*` rebinds
because mypy flags the lambda → callable substitution as an
assignment-incompatible-with-declared-type. Add a same-line reason:

```python
_k8s.batch_v1 = lambda: batch  # type: ignore[assignment]  # SPEC_LANE_STUBS path; lambda matches batch_v1()'s callable shape
```

- [ ] **Step 4: Smoke-boot.**

```bash
cd backend && SPEC_LANE_STUBS=true AUTH_DEV_MODE=true ENVIRONMENT=development \
  AUTH_DEV_EMAIL=admin@dev.local CF_ACCESS_TEAM_DOMAIN="" CF_ACCESS_APP_AUD="" \
  DATABASE_URL='sqlite+aiosqlite:///file::memory:?cache=shared&uri=true' \
  uv run python -c "
import asyncio
from app.main import app

async def boot():
    async with app.router.lifespan_context(app):
        assert hasattr(app.state, 'stub_batch')
        assert hasattr(app.state, 'stub_core')
        assert hasattr(app.state, 'stub_volcano')
        from app.services import k8s
        # core_v1() should return the stub.
        assert k8s.core_v1() is app.state.stub_core
        # MLflow should be the stub.
        assert app.state.mlflow.__class__.__name__ == 'StubMlflowClient'
        print('OK')

asyncio.run(boot())
"
```

Output: `OK`.

**Verification:**

- ruff + mypy clean.
- Smoke boot prints `OK`.
- No `BACKEND_ERRORS` bumps in the boot logs (a real-cluster call
  would bump them; the stubs accept all calls).

### Task 4: Refactor the integration conftest to import from `_stubs`

**Files:**

- Modify: `backend/tests/integration/conftest.py`

- [ ] **Step 1: Replace `_mock_k8s_load_config` body with an import.**

```python
@pytest.fixture(autouse=True)
def _mock_k8s_load_config(monkeypatch):
    from app.services._stubs import safe_load_config

    monkeypatch.setattr("app.services.k8s.load_config", safe_load_config)
```

- [ ] **Step 2: Replace `mock_k8s_batch` body with the shared classes.**

```python
@pytest.fixture(autouse=True)
def mock_k8s_batch(monkeypatch):
    from app.services._stubs import (
        CALLER_MODULE_REBIND_TARGETS,
        StubBatch,
        StubCore,
        StubVolcano,
    )

    batch = StubBatch()
    core = StubCore()
    volcano = StubVolcano()

    monkeypatch.setattr("app.services.k8s.batch_v1", lambda: batch)
    monkeypatch.setattr("app.services.k8s.core_v1", lambda: core)
    monkeypatch.setattr("app.services.k8s.volcano_v1alpha1", lambda: volcano)

    for module_path, name in CALLER_MODULE_REBIND_TARGETS:
        target = {"batch_v1": batch, "core_v1": core, "volcano_v1alpha1": volcano}[name]
        monkeypatch.setattr(f"{module_path}.{name}", lambda t=target: t)
```

- [ ] **Step 3: Replace `mock_mlflow` `_Stub` definition with the shared
      class.**

Keep the `no_mock_mlflow` branch unchanged. Replace the inline `_Stub`
class with:

```python
from app.services._stubs import StubMlflowClient

# ... no_mock_mlflow branch unchanged ...

stub = StubMlflowClient()
fastapi_app.dependency_overrides[get_mlflow] = lambda: stub
yield stub
fastapi_app.dependency_overrides.pop(get_mlflow, None)
```

- [ ] **Step 4: Run the full integration tier.**

```bash
cd backend && uv run pytest tests/integration -x --tb=short
```

All tests must pass. If a test fails, the import path in `_stubs.py`
diverged from the original — fix in `_stubs.py`, not in the test.

- [ ] **Step 5: Run the contract + heavy tiers as a sanity check.**

```bash
cd backend && uv run pytest tests/contract -x --tb=short
cd backend && uv run pytest tests/heavy -m heavy -x --tb=short  # may skip without docker
```

The contract + heavy tiers should be unaffected (they don't use the
integration autouse fixtures), but a refactor that accidentally
imports `_stubs` from `tests/conftest.py` would break them.

**Verification:**

- All integration tests pass with no behaviour change.
- Contract + heavy tiers unchanged.
- `_stubs` is imported only from the integration conftest (grep):

```bash
grep -rn "from app.services._stubs\|import _stubs" backend
# Expect: backend/app/main.py, backend/tests/integration/conftest.py,
# (Task 5 will add: backend/tests/integration/services/test_stubs_module.py)
```

### Task 5: Add direct-test for the shared module

**Files:**

- Create: `backend/tests/integration/services/test_stubs_module.py`

- [ ] **Step 1: Create the test file.**

Six test cases, listed in spec §6.6. Use the existing pytest-asyncio
fixtures pattern (no new conftest needed — placing the file under
`tests/integration/services/` picks up the integration conftest's
autouse fixtures, but the new tests don't depend on them; they
instantiate stubs directly).

```python
"""Direct-call tests for app.services._stubs.

The shared module is imported by both pytest's integration conftest
and the FastAPI lifespan (when SPEC_LANE_STUBS=true). These tests
guard the behavioural contract so a refactor in one consumer can't
silently change the other.
"""
import pytest
from kubernetes.client.exceptions import ApiException

from app.services._stubs import (
    StubBatch,
    StubCore,
    StubMlflowClient,
    StubVolcano,
    safe_load_config,
)


def test_stub_batch_create_then_read_404():
    batch = StubBatch()
    batch.create_namespaced_job("ns", {"metadata": {"name": "job-1"}})
    batch.delete_namespaced_job("job-1", "ns")
    with pytest.raises(ApiException) as exc_info:
        batch.read_namespaced_job("job-1", "ns")
    assert exc_info.value.status == 404


def test_stub_core_secret_patches_recorded():
    core = StubCore()
    core.patch_namespaced_secret("sec-1", "ns", {"meta": "owner-ref"})
    assert core.secret_patches == [("sec-1", "ns", {"meta": "owner-ref"})]


def test_stub_volcano_create_then_list():
    volcano = StubVolcano()
    volcano.create_namespaced_custom_object(
        "batch.volcano.sh", "v1alpha1", "ns", "jobs",
        {"metadata": {"name": "vcjob-1"}},
    )
    listed = volcano.list_namespaced_custom_object("batch.volcano.sh", "v1alpha1", "ns", "jobs")
    assert len(listed["items"]) == 1


def test_stub_volcano_get_returns_404_by_default():
    volcano = StubVolcano()
    with pytest.raises(ApiException) as exc_info:
        volcano.get_namespaced_custom_object("batch.volcano.sh", "v1alpha1", "ns", "jobs", "missing")
    assert exc_info.value.status == 404


@pytest.mark.asyncio
async def test_stub_mlflow_get_or_create_experiment_increments_counter():
    stub = StubMlflowClient()
    exp_id_1 = await stub.get_or_create_experiment("exp-A")
    exp_id_2 = await stub.get_or_create_experiment("exp-B")
    assert exp_id_1 != exp_id_2
    assert "exp-A" in stub.experiment_creates
    assert "exp-B" in stub.experiment_creates


def test_safe_load_config_swallows_config_exception():
    # On the test runner there is no kubeconfig; safe_load_config must
    # not raise.
    safe_load_config()  # no assertion — the absence of an exception is the contract
```

- [ ] **Step 2: Run the new tests.**

```bash
cd backend && uv run pytest tests/integration/services/test_stubs_module.py -v
```

All six cases pass.

- [ ] **Step 3: Re-run the full integration tier to catch interaction
      bugs.**

```bash
cd backend && uv run pytest tests/integration -x --tb=short
```

**Verification:**

- 6 new tests green.
- Full integration tier still green.
- ruff + mypy clean on the new file.

### Task 6: Turn on `SPEC_LANE_STUBS` in the Playwright live-stack + CI

**Files:**

- Modify: `frontend/playwright.config.ts`
- Modify: `.github/workflows/frontend-slow.yml`

- [ ] **Step 1: Add `SPEC_LANE_STUBS=true` to the webServer env in
      `playwright.config.ts`.**

Inside the backend webServer block's `env`:

```ts
env: {
  AUTH_DEV_MODE: "true",
  AUTH_DEV_EMAIL: "admin@dev.local",
  ENVIRONMENT: "development",
  DATABASE_URL:
    "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
  CF_ACCESS_TEAM_DOMAIN: "",
  CF_ACCESS_APP_AUD: "",
  DOCS_ENABLED: "true",
  SPEC_LANE_STUBS: "true",  // installs in-process K8s + MLflow stubs
},
```

- [ ] **Step 2: Add the same env to the workflow step.**

In `.github/workflows/frontend-slow.yml`, the "Run playwright" step
env block:

```yaml
env:
  E2E_BASE_URL: http://127.0.0.1:5173
  DOCS_ENABLED: "true"
  SPEC_LANE_STUBS: "true"
```

- [ ] **Step 3: Run the affected Playwright spec locally.**

```bash
cd frontend && pnpm playwright test tests/e2e/jobs/full-lifecycle.spec.ts --reporter=list
```

Expect green. The spec asserts `submitResp.status() === 202`, which
only works when MLflow is stubbed (today it 500s).

- [ ] **Step 4: Verify nothing leaked onto the cluster.**

```bash
kubectl get vcjob -A 2>/dev/null | grep -c "playwright\|test-" || echo "OK: no test resources"
kubectl get secret -n lolday-jobs 2>/dev/null | grep -c "job-" || echo "OK: no test secrets"
```

Both must say `OK`.

- [ ] **Step 5: Smoke-test a non-job-submitting spec to confirm the
      flag doesn't regress anything.**

```bash
cd frontend && pnpm playwright test tests/e2e/auth/role-based-visibility.spec.ts --reporter=list
```

Expect green.

**Verification:**

- `jobs/full-lifecycle.spec.ts` green.
- No new K8s resources on the operator cluster.
- A read-only spec still green.

### Task 7: Update docs

**Files:**

- Modify: `docs/architecture.md`
- Modify: `.claude/rules/testing.md`

- [ ] **Step 1: Add §10 #34 entry (frontend-slow stub layer resolved).**

In `docs/architecture.md` §10 (after entry #33, increment the
counter):

```md
34. ~~**frontend-slow live-stack k8s/MLflow stub layer**~~ — resolved
    2026-05-17 in PR #NNN. `backend/app/services/_stubs.py` extracts
    the in-memory K8s + MLflow stub classes that had lived inline in
    `backend/tests/integration/conftest.py` since Phase 1 D1.2.
    `app.config.Settings.SPEC_LANE_STUBS` (default `False`, refused in
    production by `validate_sso_config`) controls a lifespan hook in
    `backend/app/main.py` that installs the stubs as `app.state`
    singletons before the reconciler / FIFO scheduler tasks start,
    rebinding `app.services.k8s.{batch_v1,core_v1,volcano_v1alpha1}`
    and the matching `from-import` targets in 9 caller modules to
    point at the singletons. `app.state.mlflow` is replaced with
    `StubMlflowClient()` (skipping the real `MlflowClient.from_settings`
    construction). `frontend/playwright.config.ts` + the
    `.github/workflows/frontend-slow.yml` step now set
    `SPEC_LANE_STUBS=true`, so the Playwright live-stack runs without
    a real cluster (was leaking real Volcano CRs onto server30 every
    `pnpm playwright test` and CR-bumping
    `BACKEND_ERRORS{stage="fifo_scheduler_iteration"}` on the
    no-kubeconfig CI runner). Six direct-call tests on the shared
    module live in
    `backend/tests/integration/services/test_stubs_module.py`. Closes
    `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`
    §10 D2.9. Spec:
    `docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md`.
```

- [ ] **Step 2: Add the §13 entry in `.claude/rules/testing.md`.**

After the existing rules:

```md
## 13. Shared K8s + MLflow stubs

`backend/app/services/_stubs.py` is the single source of truth for the
in-memory K8s + MLflow stubs used by:

- pytest integration tier (`backend/tests/integration/conftest.py`
  autouse fixtures — per-test instances, isolation via `monkeypatch`)
- Playwright live-stack (`SPEC_LANE_STUBS=true` lifespan install in
  `backend/app/main.py` — process-scoped singletons on `app.state`)

If you add a new module that does
`from app.services.k8s import {batch_v1, core_v1, volcano_v1alpha1}`,
add the matching entry to `_stubs.CALLER_MODULE_REBIND_TARGETS`. Both
consumers pick up the new entry automatically. Tests that don't
rebind the new module will reach the real K8s API and either crash
in CI or leak resources locally — flag the missing entry in PR
review.

The `SPEC_LANE_STUBS` flag is refused in production
(`Settings.validate_sso_config`). Do not invoke
`_install_spec_lane_stubs(app)` outside the lifespan path.
```

- [ ] **Step 3: Run docs-style sanity.**

```bash
pre-commit run --files docs/architecture.md .claude/rules/testing.md
```

Markdown formatting clean.

**Verification:**

- `docs/architecture.md` §10 #34 reads cleanly.
- `.claude/rules/testing.md` §13 added.
- pre-commit clean on both files.

---

## Running locally

End-to-end smoke after Tasks 1–7 are merged:

```bash
cd frontend
SPEC_LANE_STUBS=true pnpm playwright test tests/e2e/jobs/full-lifecycle.spec.ts
```

Expected: 1 passed, ~10s.

Then verify the cluster is unaffected:

```bash
kubectl get vcjob -A | wc -l   # same as before
kubectl get secret -n lolday-jobs | grep job- | wc -l   # same as before
```

---

## Conventions

- Branch: `feat/frontend-slow-stub-layer`
- Commits (one per task, conventional-commit format):
  - `feat(backend): extract k8s+mlflow stubs into app.services._stubs`
  - `feat(backend): add SPEC_LANE_STUBS setting + production refusal`
  - `feat(backend): install spec-lane stubs at lifespan start`
  - `refactor(tests): integration conftest imports from app.services._stubs`
  - `test(backend): direct-call tests for app.services._stubs`
  - `feat(ci): enable SPEC_LANE_STUBS in frontend-slow live-stack`
  - `docs(architecture): close §10 frontend-slow stub layer`
- PR title: `feat(backend): frontend-slow live-stack k8s/MLflow stub layer`
- PR body: include `Spec:` + `Plan:` lines per `docs/conventions.md` §3.

## Risks + mitigations (per spec §7 recap)

| Risk                                                         | Mitigation                                                                                        |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Production receives `SPEC_LANE_STUBS=true`                   | `Settings.validate_sso_config` raises at boot; pod CrashLoopBackOff (loud, not silent)            |
| A new K8s caller module skips `CALLER_MODULE_REBIND_TARGETS` | Integration tests fail in the same way the live-stack would — single failure mode, easy to spot   |
| Stub diverges from real K8s / MLflow REST                    | Contract tier (`backend/tests/contract/mlflow/`) + heavy tier (`backend/tests/heavy/mlflow/`)     |
| Lifespan import order surprise                               | `_install_spec_lane_stubs` is called _before_ any `app.state.mlflow` / reconciler / scheduler use |
| Mypy `[assignment]` ignore in `main.py`                      | Same-line reason comment per `.claude/rules/backend.md` §Lint/format/type-check discipline        |

## Acceptance gate

- All seven tasks have their verification block green.
- `cd frontend && pnpm playwright test` passes locally (full suite, not
  just `full-lifecycle`).
- `kubectl get vcjob -A` shows no new resources after a local run.
- CI workflows green:
  - `lint` (pre-commit clean)
  - `backend-fast` (integration tier unchanged in behaviour)
  - `frontend-fast` (no change there)
  - `helm` (no chart change)
  - `images / *`, `helpers / *`, `gitleaks` (existing)
- `frontend-slow.yml` runs informational; the `full-lifecycle` spec
  now passes on first attempt (the proof the stub layer is wired).
