# Testing rules

Path scope: anything under `backend/tests/`, `frontend/tests/`,
`charts/lolday/tests/`, `tests/`, plus all `*.test.tsx`, `*.spec.ts`,
`test_*.py`, `*_test.yaml`.

Source spec:
`docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md`.

## Twelve anti-flaky rules

1. **No network in tests.** backend → `respx` with `assert_all_called=True`;
   frontend → MSW (`frontend/tests/mocks/handlers.ts`) and a `globalSetup` that
   intercepts `fetch` / `XMLHttpRequest` and fails on un-mocked calls.
2. **Time is injected.** Use `freezegun.freeze_time` for backend, vitest
   fake timers for frontend, `clock.install()` for playwright. Never
   read the wall clock.
3. **Deterministic random seeds.** Configure `hypothesis` profile;
   `fake = Faker(); fake.seed_instance(42)`; `vi.useFakeTimers()`.
4. **Order-independent tests.** `pytest-randomly` is in `addopts`;
   reshuffle every run. If a test breaks under reshuffle, fix the fixture
   leak — do not pin the order.
5. **Eventually-consistent waits poll.** `wait_for(condition, timeout=10)` /
   `expect.toHaveCount()` / `waitFor(...)` — never `time.sleep`.
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
```

A flaky-tracked test **must** carry both markers and a linked issue:

```python
@pytest.mark.flaky(reruns=2)
@pytest.mark.flaky_tracked(issue="https://github.com/bolin8017/lolday/issues/N")
def test_xxx():
    ...
```

`backend/tests/conftest.py` (the root one) installs a `pytest_collection_modifyitems`
hook that rejects `flaky_tracked` without an `issue` kwarg.

`flaky-tracker.yml` (created in D1.13; weekly cron) will aggregate the last
7 days of JUnit XML; any test with failure rate > 1 % gets an auto-issue with the `flaky` label.
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
- playwright stays `fullyParallel: false` until R4 (Phase 2) + D3.4 (Phase 3) land.

## Test execution telemetry

`test-telemetry.yml` (created in D4.4; weekly cron) will ingest `--junitxml` artifacts and write
`docs/test-telemetry/dashboard.md` with P50/P95/P99 timings, 7-day failure
rate, and slow-test ranking. Use the dashboard to decide what to refactor
or retire.

## Per-area required tests

When you touch the listed area, the corresponding test type **must** be
present in the same PR. Path-filtered triggers in `dispatch.yml` (created
in D1.12) enforce this in CI:

| Touched path                            | Required additional test                  |
| --------------------------------------- | ----------------------------------------- |
| `backend/app/routers/*.py`              | contract/openapi schemathesis case        |
| `backend/app/reconciler/*.py`           | reconciler integration test               |
| `backend/migrations/*.py`               | up/down roundtrip + real-PG heavy migrate |
| `frontend/src/api/schema.gen.ts`        | contract/schema_gen_drift                 |
| `charts/lolday/templates/<resource>/**` | helm-unittest suite for `<resource>`      |
| `scripts/*.sh`                          | bats unit (after Phase 4)                 |

## 13. Shared K8s + MLflow stubs

`backend/app/services/_stubs.py` is the single source of truth for the
in-memory K8s + MLflow stubs used by:

- the pytest integration tier — `backend/tests/integration/conftest.py`
  autouse fixtures (per-test instances, isolation via `monkeypatch`)
- the Playwright live-stack — `SPEC_LANE_STUBS=true` lifespan install
  in `backend/app/main.py::_install_spec_lane_stubs` (process-scoped
  singletons on `app.state`)

If you add a new caller module that does
`from app.services.k8s import {batch_v1, core_v1, volcano_v1alpha1}`,
add the matching entry to `_stubs.CALLER_MODULE_REBIND_TARGETS`. Both
consumers pick up the new entry automatically. Tests that don't
rebind the new module will reach the real K8s API and either crash
in CI or leak resources locally — flag the missing entry in PR
review.

The `SPEC_LANE_STUBS` flag is refused in production
(`Settings.validate_sso_config`). Do not invoke
`_install_spec_lane_stubs(app)` outside the lifespan path.

Spec: `docs/superpowers/specs/2026-05-17-frontend-slow-stub-layer-design.md`.
