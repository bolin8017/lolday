# Reconciler Module Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is self-contained: move code → update test patches → run targeted tests → run full backend tests → run pre-commit → commit.

**Goal:** Split `backend/app/reconciler.py` (1467 lines, 57960 bytes) into a `backend/app/reconciler/` Python package of cohesive submodules with **zero behavior change**. Each submodule ≤ 15KB. The 9 existing test files (`backend/tests/test_reconciler*.py`) plus `test_service_token_notify.py` and `test_metrics.py` are the safety net — they MUST stay green at every commit.

**Architecture:** Convert `app/reconciler.py` (single 57KB module) → `app/reconciler/` (package). The package's `__init__.py` re-exports the public API surface (everything currently importable as `from app.reconciler import X`), so callers (`app/main.py`, every test file) keep working unchanged. Test `patch("app.reconciler.X", ...)` calls update to `patch("app.reconciler.<submodule>.X", ...)` because Python's `unittest.mock.patch` resolves names where they're looked up, not where they're re-exported. **One Extract Function** (`_finalize_clean_scan`) is required because `_handle_succeeded` alone is 15.8KB; this is a structural refactor with no behavior change, flagged below for sign-off.

**Tech Stack:**

- Python 3.12, FastAPI, SQLAlchemy 2.0 async, asyncpg/aiosqlite
- Tooling: ruff (root `ruff.toml`), mypy (root `mypy.ini`), pytest with `asyncio_mode=auto`, pre-commit
- Tests: `cd backend && uv run pytest`. MLflow autouse-mocked. Test DB is aiosqlite.
- Hygiene baseline (verified before plan): `uv run ruff check backend/` clean, `uv run --project backend mypy --config-file mypy.ini` clean (with `[mypy-app.reconciler] ignore_errors = true` active).

---

## Critical decision flagged for review (read first)

**Single Extract Function: `_finalize_clean_scan`**

`_handle_succeeded` is 332 lines / 15837 bytes — by itself it busts the 15KB-per-file target. Putting it whole in any single file produces a >15KB file.

Resolution: Extract the scan-SUCCESS branch (the second half of the current `_handle_succeeded`, lines 277–537 in `reconciler.py`) into a new helper `_finalize_clean_scan(session, b, harbor, detector, digest, scan)` placed in `build_finalize.py`. The slim `_handle_succeeded` keeps lines 207–275 (pre-scan setup + scan-status dispatch) and ends with `await _finalize_clean_scan(session, b, harbor, detector, digest, scan)` for the SUCCESS path.

- This is a textbook **Extract Function** refactor. Behavior is preserved bit-for-bit: same status transitions, same DB writes, same notification fire-and-forget, same `_cleanup_build_secret` call, same control flow.
- The 9 existing build/notify/manifest tests verify each branch and stay green without modification (other than patch-target updates per the test-patch map below).
- Justification for crossing the "no logic change" line: the user's brief says `拆完後每個檔 < 15KB` is mandatory, and this is the minimum-invasive way to meet it.

**Alternative if the operator vetoes the Extract Function:**

- Accept `builds.py` ≈ 22KB (over the 15KB target by ~7KB). Pure file moves, no Extract Function. Tradeoff: misses size target but maximally conservative on behavior.

The plan below assumes the Extract Function is approved. If vetoed, skip Task 8 (`build_finalize.py`) and merge its content back into `builds.py`.

---

## Out of scope (explicit list)

- Any logic change to status transitions, side-effect ordering, error handling semantics
- Bug fixes (record any observed issues in the plan's "Findings" section at the end; open separate phase)
- New features, new tests, new assertions
- Public API surface changes (every name currently importable from `app.reconciler` MUST remain importable)
- Removing the mypy `[mypy-app.reconciler] ignore_errors = true` override and fixing the 20 union-attr/arg-type errors → see [`docs/architecture.md`](../../architecture.md) §9 #11; deferred to a separate follow-up phase
- Removing duplicate `_fail_build_with_notify`-style boilerplate from `_handle_failed`/`_handle_timeout`/the 5 fail-closed branches in `_finalize_clean_scan` (extract-helper refactor; defer to follow-up)
- Changing `services/notify.py`, `services/harbor.py`, `services/k8s.py`, or any other service module
- Renaming any function or constant (preserves grep-ability for ops review)

---

## Final file structure

| File                | Approx. lines | Approx. bytes | Contents                                                                                                                                                                                                            |
| ------------------- | ------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`       | ~40           | ~1.5KB        | Module docstring + re-export `__all__`                                                                                                                                                                              |
| `notify.py`         | ~100          | ~4KB          | `NotifyContext`, `_user_context`, `_detector_label`, `_ui_url`, `_primary_metric`, `_fire_job_failed_notify`                                                                                                        |
| `log_capture.py`    | ~110          | ~4.5KB        | `_container_from_failure_reason`, `_capture_pod_logs`, `_capture_log_tail`, `_capture_job_log_tail`                                                                                                                 |
| `builds.py`         | ~180          | ~8.5KB        | `IN_FLIGHT`, `reconcile_build`, `_handle_succeeded` (slim — scan-status dispatch), `_handle_failed`, `_handle_timeout`, `_update_progress`, `_extract_failure_reason`, `_cleanup_build_secret`                      |
| `build_finalize.py` | ~270          | ~14KB         | `_finalize_clean_scan` (NEW Extract Function; contains the CVE-blocked + promotion + success-commit + secret-cleanup paths)                                                                                         |
| `jobs.py`           | ~330          | ~13KB         | `reconcile_job`, `_job_timed_out`, `_check_event_terminal`, `_update_job_progress`, `_handle_job_succeeded`, `_register_model_from_job`, `_handle_job_failed`, `_extract_job_failure_reason`, `_cleanup_job_secret` |
| `projections.py`    | ~140          | ~5.5KB        | `_project_summary_metrics`, `_read_mlflow_artifact`, `_project_prediction_summary`                                                                                                                                  |
| `orphans.py`        | ~125          | ~5.7KB        | `ORPHAN_GRACE_SECONDS`, `reconcile_orphan_vcjobs`                                                                                                                                                                   |
| `model_sync.py`     | ~30           | ~1.6KB        | `sync_model_versions`                                                                                                                                                                                               |
| `loop.py`           | ~60           | ~3.3KB        | `SYNC_EVERY_N_ITERATIONS`, `ORPHAN_SCAN_EVERY_N_ITERATIONS`, `RECONCILER_WAIT_SECONDS`, `reconciler_loop`                                                                                                           |

**Module dependency graph (no cycles):**

```
loop.py
  ├── builds.py        ── notify.py, log_capture.py, build_finalize.py
  │     └── build_finalize.py  ── notify.py, log_capture.py
  ├── jobs.py          ── notify.py, log_capture.py, projections.py
  ├── orphans.py       ── (services only)
  └── model_sync.py    ── (services only)

projections.py         ── (services only)
notify.py              ── (services only)
log_capture.py         ── (services only)
```

Extraction order is **leaves first**: notify → log_capture → projections → orphans → model_sync → build_finalize → builds → jobs → loop.

---

## Public API contract (must remain importable as `from app.reconciler import X`)

| Name                          | Imported by                                                                                                      | New canonical home |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------ |
| `reconciler_loop`             | `app/main.py:10`                                                                                                 | `loop.py`          |
| `reconcile_build`             | `test_reconciler.py`, `test_reconciler_manifest.py`, `test_service_token_notify.py`, `test_reconciler_notify.py` | `builds.py`        |
| `reconcile_job`               | `test_reconciler_jobs.py`, `test_reconciler_events.py`, `test_reconciler_notify.py`                              | `jobs.py`          |
| `reconcile_orphan_vcjobs`     | `test_reconciler_orphans.py`                                                                                     | `orphans.py`       |
| `_project_summary_metrics`    | `test_reconciler_summary_projection.py`                                                                          | `projections.py`   |
| `_project_prediction_summary` | `test_reconciler_prediction_summary.py`                                                                          | `projections.py`   |
| `_fire_job_failed_notify`     | `test_service_token_notify.py`                                                                                   | `notify.py`        |
| `_user_context`               | `test_service_token_notify.py`                                                                                   | `notify.py`        |
| `_handle_failed`              | `test_reconciler_log_capture.py`, `test_reconciler_notify.py`                                                    | `builds.py`        |
| `_handle_succeeded`           | `test_reconciler_log_capture.py`, `test_reconciler_notify.py`                                                    | `builds.py` (slim) |
| `_handle_job_failed`          | `test_reconciler_notify.py`                                                                                      | `jobs.py`          |
| `_handle_job_succeeded`       | `test_reconciler_notify.py`                                                                                      | `jobs.py`          |
| `_handle_timeout`             | `test_reconciler_notify.py`                                                                                      | `builds.py`        |
| `_capture_pod_logs`           | `test_reconciler_log_capture.py`                                                                                 | `log_capture.py`   |
| `_capture_log_tail`           | `test_reconciler_log_capture.py`                                                                                 | `log_capture.py`   |

`__init__.py` re-exports each via `from .<submodule> import X` so all import statements above keep working unchanged.

---

## Test patch update map

`unittest.mock.patch` resolves names at the location they're **looked up**, not at the module that re-exports them. After the split, each `patch("app.reconciler.X", ...)` must point at the submodule that _uses_ X. The mapping below is exhaustive — there are 70 patch sites total (verified via `grep "patch(" backend/tests/test_reconciler*.py backend/tests/test_service_token_notify.py backend/tests/test_metrics.py`).

| Patch target (current)                                                                                | New target                                                                                                                                                                                                                                                      | Reason                                                                                                                                                                                                                                         |
| ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app.reconciler.batch_v1`                                                                             | `app.reconciler.builds.batch_v1`                                                                                                                                                                                                                                | `batch_v1` is used inside `reconcile_build` and `_handle_timeout` (build path only)                                                                                                                                                            |
| `app.reconciler.HarborClient`                                                                         | `app.reconciler.builds.HarborClient`                                                                                                                                                                                                                            | Only used in `_handle_succeeded`                                                                                                                                                                                                               |
| `app.reconciler.core_v1` (test_reconciler.py, test_reconciler_manifest.py, test_reconciler_notify.py) | `app.reconciler.builds.core_v1` (in build-path tests) **OR** `app.reconciler.log_capture.core_v1` (when patching log capture)                                                                                                                                   | `core_v1` is used in builds (`_update_progress`, `_extract_failure_reason`, `_cleanup_build_secret`) AND in `log_capture.py` (`_capture_pod_logs`). Keep one-patch-per-call-site by patching the module that contains the function under test. |
| `app.reconciler.core_v1` (test_reconciler_jobs.py)                                                    | `app.reconciler.jobs.core_v1`                                                                                                                                                                                                                                   | Used in `_update_job_progress`, `_extract_job_failure_reason`, `_cleanup_job_secret`                                                                                                                                                           |
| `app.reconciler.core_v1` (test_reconciler_orphans.py)                                                 | `app.reconciler.orphans.core_v1`                                                                                                                                                                                                                                | Used in `reconcile_orphan_vcjobs` for the orphan-secret delete                                                                                                                                                                                 |
| `app.reconciler.core_v1` (test_reconciler_log_capture.py)                                             | `app.reconciler.log_capture.core_v1`                                                                                                                                                                                                                            | Used in `_capture_pod_logs`                                                                                                                                                                                                                    |
| `app.reconciler.volcano_v1alpha1` (test_reconciler_jobs.py, test_reconciler_notify.py at line 407)    | `app.reconciler.jobs.volcano_v1alpha1`                                                                                                                                                                                                                          | Used in `reconcile_job`                                                                                                                                                                                                                        |
| `app.reconciler.volcano_v1alpha1` (test_reconciler_orphans.py)                                        | `app.reconciler.orphans.volcano_v1alpha1`                                                                                                                                                                                                                       | Used in `reconcile_orphan_vcjobs`                                                                                                                                                                                                              |
| `app.reconciler.notify_build_completed`                                                               | `app.reconciler.builds.notify_build_completed`                                                                                                                                                                                                                  | Used in `_handle_succeeded` (builds.py side after slim — actually used in `_finalize_clean_scan`, see below)                                                                                                                                   |
| `app.reconciler.notify_build_failed`                                                                  | Use case-by-case: `app.reconciler.builds.notify_build_failed` (used in `reconcile_build`'s 404 path and `_handle_failed`/`_handle_timeout`); `app.reconciler.build_finalize.notify_build_failed` (used in 5 fail-closed branches inside `_finalize_clean_scan`) | Multiple call sites span two modules; pick the one matching the test's branch                                                                                                                                                                  |
| `app.reconciler.notify_trivy_blocked`                                                                 | `app.reconciler.build_finalize.notify_trivy_blocked`                                                                                                                                                                                                            | Used only in `_finalize_clean_scan` CVE-blocked branch                                                                                                                                                                                         |
| `app.reconciler.notify_job_completed`                                                                 | `app.reconciler.jobs.notify_job_completed`                                                                                                                                                                                                                      | Used in `_handle_job_succeeded`                                                                                                                                                                                                                |
| `app.reconciler.notify_job_failed`                                                                    | `app.reconciler.notify.notify_job_failed`                                                                                                                                                                                                                       | Used inside `_fire_job_failed_notify` (which lives in `notify.py`)                                                                                                                                                                             |

**Practical rule for each patch update step:** for every `patch("app.reconciler.NAME", ...)` line, identify which function under test calls `NAME`, find that function's new home (per the function-placement table above), and substitute `app.reconciler.<that-module>.NAME`.

**Special case — `test_reconciler_notify.py` `_patch_notify` context manager (lines 25–34):** This patches all five `notify_*` functions at once. After the split, `notify_*` are imported in three different submodules (`builds.py`, `build_finalize.py`, `notify.py`, `jobs.py`). The context manager must list five distinct patch targets. Concrete updated form:

```python
with (
    patch("app.reconciler.jobs.notify_job_completed", new=AsyncMock()) as jc,
    patch("app.reconciler.notify.notify_job_failed", new=AsyncMock()) as jf,
    patch("app.reconciler.build_finalize.notify_build_completed", new=AsyncMock()) as bc,
    # notify_build_failed is used in builds.py (3 sites) AND build_finalize.py (5 sites);
    # patch BOTH so any branch under test sees a mock
    patch("app.reconciler.builds.notify_build_failed", new=AsyncMock()) as bf_b,
    patch("app.reconciler.build_finalize.notify_build_failed", new=AsyncMock()) as bf_f,
    patch("app.reconciler.build_finalize.notify_trivy_blocked", new=AsyncMock()) as tb,
):
    # The test currently asserts `bf.assert_called_once()` — combine after the with-block:
    # bf_total_calls = bf_b.call_count + bf_f.call_count
    # ... or update the assertions to reference the specific branch's mock
```

This is a meaningful patch-shape change. The behavior under test stays identical (we still mock the same five names); the test's assertion lines need to know which mock the call lands on. Treat this as the most delicate test update in the plan and verify each assertion carefully.

---

## Tasks

### Task 0: Setup — worktree, baseline verification

**Files:** none modified

**Sub-skills referenced:** `superpowers:using-git-worktrees`, `superpowers:verification-before-completion`

- [ ] **Step 1: Create worktree from main**

```bash
cd /home/bolin8017/Documents/repositories/lolday
git worktree add ../lolday-reconciler-split -b chore/reconciler-split
cd ../lolday-reconciler-split
```

Expected: new worktree at `../lolday-reconciler-split` on branch `chore/reconciler-split`.

- [ ] **Step 2: Verify backend tooling baseline is clean**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split/backend
uv run ruff check .
uv run ruff format --check .
cd ..
uv run --project backend mypy --config-file mypy.ini
cd backend
uv run pytest -q
```

Expected:

- ruff check: `All checks passed!`
- ruff format check: clean (no diff)
- mypy: `Success: no issues found in 60 source files`
- pytest: all green (record exact count for delta-checks later)

- [ ] **Step 3: Record baseline pytest count + duration**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split/backend
uv run pytest --collect-only -q 2>&1 | tail -3
```

Note the `N tests collected` number. After every subsequent task, the count must be unchanged.

- [ ] **Step 4: Verify current `app.reconciler` import surface**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend python -c "
import app.reconciler as r
expected = {
    'reconciler_loop', 'reconcile_build', 'reconcile_job', 'reconcile_orphan_vcjobs',
    '_project_summary_metrics', '_project_prediction_summary',
    '_fire_job_failed_notify', '_user_context',
    '_handle_failed', '_handle_succeeded', '_handle_job_failed', '_handle_job_succeeded',
    '_handle_timeout', '_capture_pod_logs', '_capture_log_tail',
}
missing = expected - set(dir(r))
assert not missing, f'missing names on app.reconciler: {missing}'
print('OK: all 15 public-API names present')
"
```

Expected: `OK: all 15 public-API names present`. This is the regression check we'll re-run after each task.

---

### Task 1: Convert `reconciler.py` → `reconciler/__init__.py` package shell

**Files:**

- Delete: `backend/app/reconciler.py` (via `git mv` — preserves blame)
- Create: `backend/app/reconciler/__init__.py` (initially identical to old `reconciler.py`)

**Tests:** No test changes. All imports keep working because `app.reconciler` still resolves (now as a package whose `__init__.py` contains the full code).

- [ ] **Step 1: Move file via git mv**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
mkdir backend/app/reconciler
git mv backend/app/reconciler.py backend/app/reconciler/__init__.py
```

- [ ] **Step 2: Verify package imports**

```bash
uv run --project backend python -c "import app.reconciler; print(app.reconciler.__file__)"
```

Expected: `.../backend/app/reconciler/__init__.py` (note: was `reconciler.py` before).

- [ ] **Step 3: Run public-API surface check**

Re-run the snippet from Task 0 Step 4. Expect `OK: all 15 public-API names present`.

- [ ] **Step 4: Run full backend test suite**

```bash
cd backend && uv run pytest -q
```

Expected: same green pass/fail count as Task 0 Step 2. No skipped tests, no collection errors.

- [ ] **Step 5: Update mypy override scope**

mypy section `[mypy-app.reconciler]` only matches the package's `__init__.py` after the rename. Submodules added in later tasks need the override too. Edit `mypy.ini`:

```ini
# Before:
[mypy-app.reconciler]
ignore_errors = true

# After:
[mypy-app.reconciler.*]
ignore_errors = true
```

Update the comment block above the override accordingly:

```ini
# app.reconciler.*: 20 Optional-handling errors (Detector | None,
# datetime | None, etc.) across the reconciler package (split from the
# 57 KB single-file reconciler in 2026-04-30-reconciler-split.md).
# Root-cause fix deferred to a follow-up phase.
# Tracked as tech debt in docs/architecture.md §9 #11.
```

Run mypy:

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend mypy --config-file mypy.ini
```

Expected: `Success: no issues found in 60 source files`.

- [ ] **Step 6: Update ruff per-file-ignore**

`ruff.toml` has:

```toml
"backend/app/reconciler.py" = ["E402"]
```

The path no longer exists. Change the entry to cover the package (the late `from app.models.job import ...` block at the bottom of the current `__init__.py` will be removed in Task 7 when we extract `jobs.py`, but until then the override needs to point at the new path):

```toml
"backend/app/reconciler/__init__.py" = ["E402"]
```

(After Task 10 this entry will be removed entirely.)

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend ruff check backend/
```

Expected: `All checks passed!`.

- [ ] **Step 7: Run pre-commit**

```bash
pre-commit run --all-files
```

Expected: all hooks pass (or auto-fix and re-stage; if a hook reformats, re-add and continue).

- [ ] **Step 8: Commit**

```bash
git add backend/app/reconciler/__init__.py mypy.ini ruff.toml
git status                  # confirm reconciler.py shows as renamed-to → __init__.py
git commit -m "refactor(reconciler): convert single file to package shell

Identical content moved from backend/app/reconciler.py to
backend/app/reconciler/__init__.py via git mv (preserves blame).
Adjusts mypy and ruff overrides to point at the new path.

No behavior change. All tests pass."
```

---

### Task 2: Extract `notify.py`

**Files:**

- Create: `backend/app/reconciler/notify.py`
- Modify: `backend/app/reconciler/__init__.py` — replace function bodies with `from .notify import ...` re-exports
- No test changes (the test imports of `_user_context` and `_fire_job_failed_notify` go through the re-export). `_user_context`/`_fire_job_failed_notify` are not patched anywhere — only imported.

**Functions moved (lines from old reconciler.py, byte budget ≈ 4KB):**

- `NotifyContext` (dataclass, lines 59–68)
- `_user_context` (lines 72–92)
- `_detector_label` (lines 95–102)
- `_ui_url` (lines 105–107)
- `_primary_metric` (lines 110–117)
- `_fire_job_failed_notify` (lines 120–153)

- [ ] **Step 1: Create `notify.py` with module docstring + imports + the six items**

Content of `backend/app/reconciler/notify.py`:

```python
"""Discord-notification helpers for the reconciler.

These helpers are shared by both the build path (``builds.py`` /
``build_finalize.py``) and the job path (``jobs.py``):

- :class:`NotifyContext` and :func:`_user_context` resolve a Discord identity
  from a User row, returning ``None`` for service-token principals so callers
  can early-return (machine activity does not need user-event notifications).
- :func:`_detector_label`, :func:`_ui_url`, :func:`_primary_metric` are small
  formatters used inside ``notify_*`` payloads.
- :func:`_fire_job_failed_notify` is the shared ``notify_job_failed`` dispatch
  used by all 3 terminal-failure paths in :func:`reconcile_job`
  (Volcano Failed/Aborted, wall-clock TIMEOUT, k8s_job_missing 404).
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.notify import notify_job_failed


@dataclass(frozen=True)
class NotifyContext:
    """Discord-embed identity for a single notification.

    Returned from :func:`_user_context`; ``None`` from that helper means
    "skip notify" (the user is a CF Access service-token principal whose
    events would only dilute the user-event channel).
    """

    name: str
    discord_id: str | None


async def _user_context(session: AsyncSession, user_id) -> NotifyContext | None:
    """Resolve a notification identity, or ``None`` to signal "skip notify".

    ``name`` falls back through display_name → email local-part → literal
    "user" (the last case only triggers when the user row is missing
    entirely, since email is required on User).

    Service-token principals yield ``None`` so every notify_* callsite
    can early-return. Service-token activity is automated and not
    actionable by humans — its events would only dilute the user-event
    Discord channel.
    """
    from app.models import User

    user = await session.get(User, user_id)
    if user is None:
        return NotifyContext(name="unknown", discord_id=None)
    if user.is_service_token:
        return None
    name = user.display_name or (user.email.split("@")[0] if user.email else "user")
    return NotifyContext(name=name, discord_id=user.discord_user_id)


async def _detector_label(session: AsyncSession, detector_id) -> str:
    """Returns detector.display_name, or "unknown" if the row was deleted."""
    from app.models import Detector

    det = await session.get(Detector, detector_id)
    if det is None:
        return "unknown"
    return det.display_name


def _ui_url(path: str) -> str:
    """Absolute UI link built from `settings.LOLDAY_UI_BASE_URL`."""
    return f"{settings.LOLDAY_UI_BASE_URL.rstrip('/')}{path}"


def _primary_metric(metrics: dict) -> tuple[str, float] | None:
    """Returns the first available metric in priority order f1 > accuracy >
    precision > recall; None if none are numeric."""
    for key in ("f1", "accuracy", "precision", "recall"):
        val = metrics.get(key)
        if isinstance(val, int | float):
            return (key, float(val))
    return None


async def _fire_job_failed_notify(
    session: AsyncSession,
    j,
    reason: str,
) -> None:
    """Schedule a job-failed Discord notify without blocking the reconciler.

    Shared helper for the 3 terminal-failure paths: Volcano Failed/Aborted
    phase, wall-clock TIMEOUT, and k8s_job_missing (404 on GET).
    """
    from app.models import DatasetConfig, DetectorVersion

    ctx = await _user_context(session, j.owner_id)
    if ctx is None:
        return
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det_label = await _detector_label(session, dv.detector_id) if dv else "unknown"
    detector_label = f"{det_label} {dv.git_tag}" if dv else det_label
    dataset_name = None
    ds_id = j.train_dataset_id or j.test_dataset_id or j.predict_dataset_id
    if ds_id:
        ds = await session.get(DatasetConfig, ds_id)
        dataset_name = ds.name if ds else None
    asyncio.create_task(  # noqa: RUF006  # fire-and-forget notification task
        notify_job_failed(
            user_name=ctx.name,
            user_discord_id=ctx.discord_id,
            job_type=j.type.value,
            detector_label=detector_label,
            dataset_name=dataset_name,
            failure_reason=reason,
            job_url=_ui_url(f"/jobs/{j.id}"),
        )
    )
```

Note `Any` import is currently unused; remove if ruff's `F401` flags it.

- [ ] **Step 2: Update `__init__.py` to delete the moved code and add re-export**

In `backend/app/reconciler/__init__.py`:

1. Delete lines 56–153 (the entire `# ---- notify helpers ----` block and the six items).
2. Delete the now-orphaned `from sqlalchemy.ext.asyncio import AsyncSession` if no other code in `__init__.py` uses it (it's also used by other functions like `reconcile_build` so keep it).
3. After the existing imports at the top, add:

```python
from app.reconciler.notify import (
    NotifyContext,
    _detector_label,
    _fire_job_failed_notify,
    _primary_metric,
    _ui_url,
    _user_context,
)
```

(`NotifyContext` is imported for completeness even though no test imports it directly — preserves the public-namespace surface.)

- [ ] **Step 3: Verify package imports + public API surface**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend python -c "
from app.reconciler import _user_context, _fire_job_failed_notify, NotifyContext, _ui_url, _primary_metric, _detector_label
print('OK: notify re-exports work')
"
```

- [ ] **Step 4: Run targeted tests**

```bash
cd backend && uv run pytest tests/test_service_token_notify.py tests/test_reconciler_notify.py -q
```

Expected: green. (`test_service_token_notify.py` directly imports `_fire_job_failed_notify, _user_context`; `test_reconciler_notify.py` doesn't patch notify-helper internals so it's unaffected by the file move.)

- [ ] **Step 5: Run full backend test suite**

```bash
cd backend && uv run pytest -q
```

Expected: same count as baseline, all green.

- [ ] **Step 6: Run pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/notify.py backend/app/reconciler/__init__.py
git commit -m "refactor(reconciler): extract notify helpers to notify.py

Move NotifyContext, _user_context, _detector_label, _ui_url,
_primary_metric, _fire_job_failed_notify from reconciler/__init__.py
to reconciler/notify.py. __init__.py re-exports for callers.

No behavior change. No test changes (notify helpers are not patched
by name; only imported)."
```

---

### Task 3: Extract `log_capture.py`

**Files:**

- Create: `backend/app/reconciler/log_capture.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, add re-exports
- Modify: `backend/tests/test_reconciler_log_capture.py` — update 8 patches
- Modify: `backend/tests/test_reconciler.py`, `test_reconciler_manifest.py`, `test_reconciler_notify.py`, `test_reconciler_jobs.py`, `test_reconciler_orphans.py`, `test_service_token_notify.py` — only the patches that target `_capture_pod_logs` or `_capture_*log_tail`. **Most `core_v1` patches in these files do NOT belong here** — they target `core_v1` for build/job/orphan paths, which still resolve via `__init__.py`'s re-export until Tasks 7/8/4 move them.

**Functions moved (≈ 4.5KB total):**

- `_container_from_failure_reason` (lines 633–640)
- `_capture_pod_logs` (lines 643–710)
- `_capture_log_tail` (lines 713–727) — the build-pod wrapper
- `_capture_job_log_tail` (lines 1280–1294) — the job-pod wrapper

**Why both `_capture_log_tail` AND `_capture_job_log_tail` go here**: they're sibling thin wrappers around `_capture_pod_logs` with different label_selectors and main_container names. Co-locating them is the cohesive split.

- [ ] **Step 1: Create `log_capture.py`**

Content of `backend/app/reconciler/log_capture.py`:

```python
"""Pod-log capture helpers for failed/successful build and job pods.

Phase 13a A2 introduced :func:`_capture_pod_logs` as a generic helper that
tries containers in priority order (failure-reason hint → main container →
init containers) and returns whatever logs were retrievable, prefixed per
container with a ``[<container>]`` header. The build-pod wrapper
:func:`_capture_log_tail` and the job-pod wrapper :func:`_capture_job_log_tail`
are thin adapters that supply the right label selector and container names.
"""

from kubernetes.client import ApiException

from app.config import settings
from app.services.k8s import core_v1


def _container_from_failure_reason(failure_reason: str | None) -> str | None:
    """Extract container name from a failure_reason string like 'clone_failed: exit=1'."""
    if not failure_reason:
        return None
    head = failure_reason.split(":", 1)[0].strip()
    if head.endswith("_failed"):
        return head.removesuffix("_failed")
    return None


async def _capture_pod_logs(
    *,
    namespace: str,
    label_selector: str,
    main_container: str,
    init_containers: tuple[str, ...],
    failure_reason: str | None,
    tail_bytes: int,
    tail_lines: int = 200,
) -> str:
    """Capture log tail from the failing or main container of a labeled pod.

    Phase 13a A2: previous implementations hard-coded the container name
    (kaniko vs buildkit; detector only) and could not surface init-container
    output when the build/job failed before main started. This generic
    helper:
      1. Tries the container hinted by failure_reason first (e.g.
         'validate_failed' → 'validate').
      2. Falls back to main_container.
      3. Falls back to each init_container in order.
      4. Concatenates whatever logs were retrievable, prefixed with a
         '[<container>]' header line so the reader can tell what's what.
      5. Returns "" if no logs are retrievable from any container.

    The result is truncated to `tail_bytes` from the end so the persisted
    log_tail column doesn't blow up.
    """
    try:
        pods = core_v1().list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector,
        )
    except ApiException:
        return ""
    if not pods.items:
        return ""
    pod = pods.items[0]

    # Build the container query order
    hinted = _container_from_failure_reason(failure_reason)
    order: list[str] = []
    if hinted and (hinted == main_container or hinted in init_containers):
        order.append(hinted)
    if main_container not in order:
        order.append(main_container)
    for ic in init_containers:
        if ic not in order:
            order.append(ic)

    # Try each container in order; collect what we can.
    chunks: list[str] = []
    for container in order:
        try:
            log = core_v1().read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
        except ApiException:
            continue
        if log:
            chunks.append(f"[{container}]\n{log}")

    if not chunks:
        return ""
    combined = "\n\n".join(chunks)
    return combined[-tail_bytes:]


async def _capture_log_tail(b) -> str:
    """Capture build pod's log tail.

    Phase 13a A2: was hard-coded to container='kaniko' (wrong — actual
    name is 'buildkit'). Now uses the generic helper with init-container
    fallback for when builds fail in clone/validate.
    """
    return await _capture_pod_logs(
        namespace=settings.BUILD_NAMESPACE,
        label_selector=f"lolday.io/build-id={b.id}",
        main_container="buildkit",
        init_containers=("clone", "validate"),
        failure_reason=b.failure_reason,
        tail_bytes=settings.BUILD_LOG_TAIL_BYTES,
    )


async def _capture_job_log_tail(j) -> str:
    """Capture job pod's log tail.

    Phase 13a A2: previously read main 'detector' container only. Now
    also captures init-container logs (config-writer, model-fetcher) when
    the job fails before main starts.
    """
    return await _capture_pod_logs(
        namespace=settings.JOB_NAMESPACE,
        label_selector=f"lolday.job-id={j.id}",
        main_container="detector",
        init_containers=("config-writer", "model-fetcher"),
        failure_reason=j.failure_reason,
        tail_bytes=8192,
    )
```

(Type-annotation note: `b` and `j` are intentionally untyped here — at this point in the refactor `DetectorBuild` / `Job` types are still imported in `__init__.py`. The `[mypy-app.reconciler.*] ignore_errors = true` override masks this. A future type-debt phase will add the imports.)

- [ ] **Step 2: Delete moved code from `__init__.py` and add re-export**

Delete lines 633–727 (the four functions). Delete lines 1280–1294 (`_capture_job_log_tail`). Add re-export near the top:

```python
from app.reconciler.log_capture import (
    _capture_job_log_tail,
    _capture_log_tail,
    _capture_pod_logs,
    _container_from_failure_reason,
)
```

- [ ] **Step 3: Update `test_reconciler_log_capture.py` patches**

Eight patch sites at lines 39, 66, 95, 119, 136, 152, 176, 187. For each:

```python
# Before:
with patch("app.reconciler.core_v1", return_value=v1):
# After:
with patch("app.reconciler.log_capture.core_v1", return_value=v1):
```

The imports at the top stay unchanged (`from app.reconciler import _capture_pod_logs, _capture_log_tail, _handle_failed, _handle_succeeded`) — those are re-exports, and the test directly invokes the imported function (which itself looks up `core_v1` in `log_capture.py`).

The line 208 case (`from app.reconciler import _handle_failed, _handle_succeeded`) is testing build-side functions; their `core_v1` resolution still goes through `__init__.py` until Task 8. Don't change those patches yet — Task 8 will. **Important:** at line 187 specifically, examine which function is under test and which `core_v1` lookup matters. If the test invokes `_handle_failed`, `_handle_succeeded` directly, and those still live in `__init__.py` re-exporting `core_v1` from `app.services.k8s`, the patch path needs to match. Re-read the failing test output if the test breaks.

The pragmatic interim approach: patch BOTH locations during this transitional task:

```python
with (
    patch("app.reconciler.log_capture.core_v1", return_value=v1),
    patch("app.reconciler.core_v1", return_value=v1),  # remove in Task 8
):
```

This is the safest interim form. The `app.reconciler.core_v1` half drops out in Task 8 when the build path moves.

- [ ] **Step 4: Run targeted tests**

```bash
cd backend && uv run pytest tests/test_reconciler_log_capture.py -v
```

Expected: all 8 tests green.

- [ ] **Step 5: Run full backend test suite**

```bash
cd backend && uv run pytest -q
```

Expected: same baseline count, all green.

- [ ] **Step 6: pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/log_capture.py backend/app/reconciler/__init__.py backend/tests/test_reconciler_log_capture.py
git commit -m "refactor(reconciler): extract log capture helpers to log_capture.py

Move _container_from_failure_reason, _capture_pod_logs, _capture_log_tail,
_capture_job_log_tail to reconciler/log_capture.py. __init__.py re-exports
for public API. test_reconciler_log_capture.py patches updated to target
the new module location."
```

---

### Task 4: Extract `orphans.py`

**Files:**

- Create: `backend/app/reconciler/orphans.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, add re-export
- Modify: `backend/tests/test_reconciler_orphans.py` — 18 patch sites

**Functions moved (≈ 5.7KB):**

- `ORPHAN_GRACE_SECONDS` (line 1316)
- `reconcile_orphan_vcjobs` (lines 1319–1439)

- [ ] **Step 1: Create `orphans.py`**

```python
"""Orphan Volcano-job cleanup.

A schema migration / DB rebuild can leave Volcano Jobs in K8s that the
backend no longer knows about; their init containers crash on every pod
with "job not found" and KubeContainerWaiting fires forever. This module
runs a periodic scan from :func:`reconciler_loop`, lists vcjobs in the
job namespace, cross-checks each ``lolday.job-id`` label against the DB,
and deletes orphans (with their associated job-token Secret).

The :data:`ORPHAN_GRACE_SECONDS` guard skips vcjobs younger than 5 min
to avoid the create-vcjob/commit-row race in ``app/routers/jobs.py``.
"""

import logging
import uuid
from datetime import UTC, datetime

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)

logger = logging.getLogger(__name__)

ORPHAN_GRACE_SECONDS = 300  # don't touch a vcjob younger than this — see below.


async def reconcile_orphan_vcjobs(session: AsyncSession) -> int:
    """Delete Volcano Jobs whose ``lolday.job-id`` label has no matching DB row.
    [keep the docstring as in reconciler.py:1320–1340]
    """
    [body identical to reconciler.py lines 1341–1439, with one adjustment:
    move the late `from app.services.job_spec import _job_token_secret_name`
    to the top of the function — but only IF it doesn't introduce a circular
    import. If keeping the late import preserves the original behavior 1:1,
    keep it.]
```

**Important:** Copy the function body byte-for-byte from `reconciler.py:1319–1439`. Do not "improve" the late `from app.services.job_spec import _job_token_secret_name` — keep it inside the function as in the original. Do not add or remove lines except the new module-level docstring.

- [ ] **Step 2: Delete moved code + add re-export in `__init__.py`**

Delete lines 1316–1439. Add to imports:

```python
from app.reconciler.orphans import ORPHAN_GRACE_SECONDS, reconcile_orphan_vcjobs
```

- [ ] **Step 3: Update test patches**

In `backend/tests/test_reconciler_orphans.py`, all 18 patches at lines 96/97, 126/127, 151/152, 179/180, 206/207, 243/244, 278/279, 316/317, 350/351:

```python
# Before:
patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()),
patch("app.reconciler.core_v1", return_value=_CoreStub()),
# After:
patch("app.reconciler.orphans.volcano_v1alpha1", return_value=_VolcanoStub()),
patch("app.reconciler.orphans.core_v1", return_value=_CoreStub()),
```

Use `sed` for bulk replacement (verify diff before commit):

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
sed -i 's|patch("app\.reconciler\.volcano_v1alpha1"|patch("app.reconciler.orphans.volcano_v1alpha1"|g; s|patch("app\.reconciler\.core_v1"|patch("app.reconciler.orphans.core_v1"|g' backend/tests/test_reconciler_orphans.py
git diff backend/tests/test_reconciler_orphans.py | head -30  # spot-check
```

- [ ] **Step 4: Targeted tests**

```bash
cd backend && uv run pytest tests/test_reconciler_orphans.py -v
```

Expected: all green.

- [ ] **Step 5: Full backend tests**

```bash
cd backend && uv run pytest -q
```

- [ ] **Step 6: pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/orphans.py backend/app/reconciler/__init__.py backend/tests/test_reconciler_orphans.py
git commit -m "refactor(reconciler): extract orphan vcjob cleanup to orphans.py"
```

---

### Task 5: Extract `model_sync.py`

**Files:**

- Create: `backend/app/reconciler/model_sync.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, add re-export

**Functions moved (≈ 1.6KB):**

- `sync_model_versions` (lines 1442–1467)

No tests directly target `sync_model_versions` (it's exercised through the loop, indirectly).

- [ ] **Step 1: Create `model_sync.py`**

```python
"""MLflow model-registry stage sync.

The MLflow REST API allows external clients (e.g. an ML-Ops engineer using
the MLflow UI) to transition model versions between stages
(None → Staging → Production → Archived). :func:`sync_model_versions`
runs every ~60s from :func:`reconciler_loop` and reflects those
transitions back into the lolday DB so the UI shows current state.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.mlflow_client import MlflowClient


async def sync_model_versions(session: AsyncSession) -> None:
    """Pull latest stages from MLflow; reflect transitions initiated outside lolday."""
    client = MlflowClient(settings.MLFLOW_TRACKING_URI)
    from app.models import ModelVersion
    from app.models.model_registry import ModelVersionStage

    all_local = (await session.execute(select(ModelVersion))).scalars().all()
    if not all_local:
        return

    remote = await client.search_model_versions()
    by_key = {(m["name"], int(m["version"])): m for m in remote}

    for mv in all_local:
        rem = by_key.get((mv.mlflow_name, mv.mlflow_version))
        if rem is None:
            continue
        remote_stage = rem.get("current_stage", "None")
        try:
            stage_enum = ModelVersionStage(remote_stage)
        except ValueError:
            continue
        if stage_enum != mv.current_stage:
            mv.current_stage = stage_enum
            mv.last_transitioned_at = datetime.now(UTC)
    await session.commit()
```

- [ ] **Step 2: Delete from `__init__.py` + add re-export**

Delete lines 1442–1467. Add:

```python
from app.reconciler.model_sync import sync_model_versions
```

- [ ] **Step 3: Targeted tests**

```bash
cd backend && uv run pytest -q -k "sync_model_versions or model_version" 2>/dev/null || true
cd backend && uv run pytest -q  # full
```

Expected: same baseline count, all green.

- [ ] **Step 4: pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/model_sync.py backend/app/reconciler/__init__.py
git commit -m "refactor(reconciler): extract MLflow model-version sync to model_sync.py"
```

---

### Task 6: Extract `projections.py`

**Files:**

- Create: `backend/app/reconciler/projections.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, add re-export
- Modify: `backend/tests/test_reconciler_summary_projection.py` — import path stays the same; no patches to update.
- Modify: `backend/tests/test_reconciler_prediction_summary.py` — patches at lines 62 and 82 stay as inline `from app.reconciler import _project_prediction_summary` (re-export works); but if the test patches `httpx.AsyncClient` it still works (that's where the actual lookup happens — in `projections.py`).

**Functions moved (≈ 5.5KB):**

- `_project_summary_metrics` (lines 938–988)
- `_read_mlflow_artifact` (lines 991–1018)
- `_project_prediction_summary` (lines 1021–1077)

- [ ] **Step 1: Create `projections.py`**

```python
"""Read-model projections from job_events into Job.summary_metrics.

Phase 11e introduced ``Job.summary_metrics`` as a single-writer materialized
read-model populated on stage_end. The two projectors:

- :func:`_project_summary_metrics` aggregates last-per-name ``metric``,
  ``confusion_matrix``, and ``per_class`` events.
- :func:`_project_prediction_summary` reads ``predictions.csv`` from the
  succeeded predict job's MLflow run and computes a class-distribution
  summary cached under ``summary_metrics["prediction_summary"]``.

Both projectors are idempotent: running twice produces the same result.
Errors are logged + counted via ``BACKEND_ERRORS`` and never raised — the
projection is opportunistic, not part of the state-machine transition.
"""

import csv
import io
import logging
import uuid
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.job import Job

logger = logging.getLogger(__name__)


async def _project_summary_metrics(session: AsyncSession, job_id: uuid.UUID) -> None:
    [body byte-for-byte from reconciler.py:938–988]


async def _read_mlflow_artifact(run_id: str, path: str) -> str:
    [body byte-for-byte from reconciler.py:991–1018]


async def _project_prediction_summary(session: AsyncSession, j: Job) -> None:
    [body byte-for-byte from reconciler.py:1021–1077]
```

(Important: the existing `_project_summary_metrics` does `from app.models import JobEvent` lazily inside the function — keep it that way to avoid changing import-time evaluation order.)

- [ ] **Step 2: Delete from `__init__.py` + re-export**

Delete lines 938–1077. Add:

```python
from app.reconciler.projections import (
    _project_prediction_summary,
    _project_summary_metrics,
)
```

(`_read_mlflow_artifact` is an internal helper; no need to re-export — no test imports it.)

- [ ] **Step 3: Targeted tests**

```bash
cd backend && uv run pytest tests/test_reconciler_summary_projection.py tests/test_reconciler_prediction_summary.py -v
```

Expected: green.

- [ ] **Step 4: Full backend tests + pre-commit + commit**

```bash
cd backend && uv run pytest -q
pre-commit run --all-files
git add backend/app/reconciler/projections.py backend/app/reconciler/__init__.py
git commit -m "refactor(reconciler): extract metric/prediction projections to projections.py"
```

---

### Task 7: Extract `jobs.py`

**Files:**

- Create: `backend/app/reconciler/jobs.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code + the `# ====== Phase 4 ======` divider + the late imports at lines 824–829, add re-exports
- Modify: `backend/tests/test_reconciler_jobs.py` — 2 patches at lines 86/87
- Modify: `backend/tests/test_reconciler_events.py` — no patches; import-only (`from app.reconciler import reconcile_job` works through re-export). Verify nothing breaks.
- Modify: `backend/tests/test_reconciler_notify.py` — patches at lines 27 (`notify_job_completed`), 350/351 (`batch_v1`/`core_v1` for build path — leave alone), 407/408 (`volcano_v1alpha1`/`core_v1` for job path)

**Functions moved (≈ 13KB):**

- `reconcile_job` (lines 832–902)
- `_job_timed_out` (905–918)
- `_check_event_terminal` (921–935)
- `_update_job_progress` (1080–1096)
- `_handle_job_succeeded` (1099–1193)
- `_register_model_from_job` (1196–1221)
- `_handle_job_failed` (1224–1245)
- `_extract_job_failure_reason` (1248–1277)
- `_cleanup_job_secret` (1297–1313)

Note: `_capture_job_log_tail` already moved to `log_capture.py` in Task 3.

- [ ] **Step 1: Create `jobs.py`**

Module docstring template:

```python
"""Volcano vcjob reconciliation: status sync from K8s + stage_end events.

:func:`reconcile_job` runs once per ~10s loop iteration for every Job row
in a non-terminal state. The transition logic:

1. Read the Volcano vcjob via CustomObjectsApi (Phase 7.3+).
2. Wall-clock timeout check against ``settings.JOB_ACTIVE_DEADLINE_*``.
3. **Trust stage_end event before Volcano phase** (Phase 11b): if a
   ``stage_end`` JobEvent reports success/failure, transition immediately
   without consulting ``vjob.status.state.phase``. Detectors on a buggy
   exit path can finish their work but exit non-zero; the event is
   authoritative.
4. Otherwise dispatch on ``phase``: Completed → succeeded, Failed/Aborted
   /Terminated → failed, else update progress (PREPARING → RUNNING).

Terminal transitions schedule fire-and-forget Discord notifies (via
:func:`_fire_job_failed_notify` for failures; direct ``notify_job_completed``
call for success) and clean up the job-token secret.
"""
```

Module imports (top of file):

```python
import asyncio
import logging
import uuid
from datetime import UTC, datetime

from kubernetes.client import ApiException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.job import NON_TERMINAL_STATUSES, Job, JobStatus, JobType
from app.reconciler.log_capture import _capture_job_log_tail
from app.reconciler.notify import (
    _detector_label,
    _fire_job_failed_notify,
    _primary_metric,
    _ui_url,
    _user_context,
)
from app.reconciler.projections import (
    _project_prediction_summary,
    _project_summary_metrics,
)
from app.services.k8s import (
    VOLCANO_BATCH_GROUP,
    VOLCANO_BATCH_VERSION,
    VOLCANO_JOB_PLURAL,
    core_v1,
    volcano_v1alpha1,
)
from app.services.mlflow_client import MlflowClient
from app.services.notify import notify_job_completed

logger = logging.getLogger(__name__)
```

Then paste each function body byte-for-byte from `reconciler.py`. Order them as listed above (orchestrator → helpers).

- [ ] **Step 2: Delete from `__init__.py` + add re-exports**

Delete:

- Lines 824–829 (`# === Phase 4 ===` block with imports)
- Lines 832–935 (`reconcile_job`, `_job_timed_out`, `_check_event_terminal`)
- Lines 1080–1096 (`_update_job_progress`)
- Lines 1099–1245 (`_handle_job_succeeded`, `_register_model_from_job`, `_handle_job_failed`)
- Lines 1248–1277 (`_extract_job_failure_reason`)
- Lines 1297–1313 (`_cleanup_job_secret`)

Add re-exports:

```python
from app.reconciler.jobs import (
    _handle_job_failed,
    _handle_job_succeeded,
    reconcile_job,
)
```

(`_check_event_terminal`, `_job_timed_out`, etc. don't need re-export — no external import.)

- [ ] **Step 3: Update `test_reconciler_jobs.py` patches**

Lines 86/87:

```python
# Before:
patch("app.reconciler.volcano_v1alpha1", return_value=_VolcanoStub()),
patch("app.reconciler.core_v1", return_value=_CoreStub()),
# After:
patch("app.reconciler.jobs.volcano_v1alpha1", return_value=_VolcanoStub()),
patch("app.reconciler.jobs.core_v1", return_value=_CoreStub()),
```

- [ ] **Step 4: Update `test_reconciler_notify.py` patches for the job-path tests**

Lines 27 and 407/408 (the lines around the job-test cases):

```python
# Line 27 — inside _patch_notify():
patch("app.reconciler.jobs.notify_job_completed", new=AsyncMock()) as jc,

# Line 28 — _fire_job_failed_notify lives in notify.py:
patch("app.reconciler.notify.notify_job_failed", new=AsyncMock()) as jf,

# Lines 407/408:
patch("app.reconciler.jobs.volcano_v1alpha1", return_value=_Volcano()),
patch("app.reconciler.jobs.core_v1", return_value=_Core()),
```

(Lines 29, 30, 31 — the build-side `notify_*` patches and `HarborClient` — stay pointing at `app.reconciler.X` for now; Task 8 updates them.)

- [ ] **Step 5: Targeted tests**

```bash
cd backend && uv run pytest tests/test_reconciler_jobs.py tests/test_reconciler_events.py -v
cd backend && uv run pytest tests/test_reconciler_notify.py -v -k "job"
```

Expected: green for all `test_reconciler_jobs.py`, all `test_reconciler_events.py`, and the job-related tests in `test_reconciler_notify.py`. The build-related tests in `test_reconciler_notify.py` may still pass because `_patch_notify` patches both old + new locations during this transitional task — but if any fail, defer the build-side updates to Task 8 and mark the relevant tests with a temporary `@pytest.mark.xfail(reason="awaiting Task 8 builds.py extraction")` UNTIL Task 8.

**Avoid xfail if possible** — the simpler interim approach is to keep the line-29/30/31 patches as `app.reconciler.notify_build_completed` / `app.reconciler.notify_build_failed` / `app.reconciler.notify_trivy_blocked` UNCHANGED in this task. They still hit the re-export from `__init__.py` because the build code is still in `__init__.py` until Task 8.

- [ ] **Step 6: Full backend tests + pre-commit + commit**

```bash
cd backend && uv run pytest -q
pre-commit run --all-files
git add backend/app/reconciler/jobs.py backend/app/reconciler/__init__.py backend/tests/test_reconciler_jobs.py backend/tests/test_reconciler_notify.py
git commit -m "refactor(reconciler): extract job reconciliation to jobs.py

Move reconcile_job + 8 supporting functions from reconciler/__init__.py to
reconciler/jobs.py. __init__.py re-exports reconcile_job, _handle_job_*.
Test patches in test_reconciler_jobs.py and test_reconciler_notify.py
updated to target the new module location."
```

---

### Task 8: Extract `build_finalize.py` + `builds.py`

This task does TWO file moves in one commit because they're tightly coupled by the `_finalize_clean_scan` Extract Function. Doing them separately would leave an interim state where `_handle_succeeded` calls a non-existent helper.

**Files:**

- Create: `backend/app/reconciler/build_finalize.py`
- Create: `backend/app/reconciler/builds.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, add re-exports
- Modify: `backend/tests/test_reconciler.py` — 16 patches
- Modify: `backend/tests/test_reconciler_manifest.py` — 12 patches
- Modify: `backend/tests/test_reconciler_notify.py` — 4 build-path patches (lines 29/30/31, 185, 235, 350/351)
- Modify: `backend/tests/test_service_token_notify.py` — 4 patches at lines 91, 255, 256, 257

**The Extract Function:** `_handle_succeeded` (`reconciler.py:207–537`) is split:

```python
# builds.py — slim _handle_succeeded (≈ 70 lines)
async def _handle_succeeded(session: AsyncSession, b: DetectorBuild) -> None:
    """Orchestrator for builds whose K8s Job succeeded. Fetches the artifact
    digest and Harbor scan status, then either short-circuits (scan not yet
    done) or hands off to :func:`_finalize_clean_scan` for the SUCCESS path.
    """
    from app.models.detector import Detector

    detector = await session.get(Detector, b.detector_id)
    harbor = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )
    digest = await harbor.get_artifact_digest("detectors", detector.name, b.git_tag)
    if digest is None:
        b.status = DetectorBuildStatus.FAILED
        b.failure_reason = "artifact_missing_in_harbor"
        b.finished_at = datetime.now(UTC)
        await session.commit()
        return

    scan = await harbor.get_scan("detectors", detector.name, digest)
    if scan.status in {ScanStatus.NOT_SCANNED, ScanStatus.ERROR}:
        # ERROR means a prior scan terminally failed (most often: transient
        # Trivy DB cache-lock timeout). Must NEVER promote — critical=0 in
        # that case is "we didn't learn anything," not "clean." The caller's
        # wall-clock check at reconcile_build bounds the retry loop.
        if scan.status == ScanStatus.ERROR:
            BACKEND_ERRORS.labels(stage="harbor_scan_error_retry").inc()
            logger.warning(
                "Harbor returned scan_status=Error for build=%s detector=%s digest=%s "
                "— retriggering scan (not promoting)",
                b.id, detector.name, digest,
            )
        try:
            await harbor.trigger_scan("detectors", detector.name, digest)
        except httpx.HTTPError as e:
            BACKEND_ERRORS.labels(stage="harbor_trigger_scan").inc()
            logger.warning(
                "trigger_scan failed for build=%s detector=%s digest=%s: %s "
                "(will retry next reconcile cycle)",
                b.id, detector.name, digest, e,
            )
            return
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return
    if scan.status in {ScanStatus.PENDING, ScanStatus.RUNNING}:
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return
    if scan.status != ScanStatus.SUCCESS:
        BACKEND_ERRORS.labels(stage="harbor_scan_unhandled_status").inc()
        logger.error(
            "unhandled Harbor scan status %s for build=%s detector=%s digest=%s",
            scan.status, b.id, detector.name, digest,
        )
        b.status = DetectorBuildStatus.SCANNING
        await session.commit()
        return

    # Scan SUCCESS — finalize (CVE block or promote)
    await _finalize_clean_scan(session, b, harbor, detector, digest, scan)
```

```python
# build_finalize.py — _finalize_clean_scan (≈ 260 lines, the body extracted
# from reconciler.py:277–537 verbatim, just reframed as a function)
async def _finalize_clean_scan(
    session: AsyncSession,
    b: DetectorBuild,
    harbor: HarborClient,
    detector: Detector,
    digest: str,
    scan,
) -> None:
    """Finalize a build whose Harbor scan returned SUCCESS.

    Either:
    - Marks the build CVE_BLOCKED + deletes the artifact (when scan.critical > 0).
    - Or runs the promotion path: idempotency check, manifest decode, version
      creation. 5 fail-closed branches handle harbor_labels_fetch_failed,
      manifest_label_missing, manifest_invalid, git_sha_label_missing, and
      digest mismatch.
    Always calls :func:`_cleanup_build_secret` (or its caller does) on exit.

    Extracted from the original ``_handle_succeeded`` in 2026-04-30
    reconciler-split for module-size budget. Behavior is preserved 1:1.
    """
    if scan.critical > 0:
        [body byte-for-byte from reconciler.py:277–297]
    else:
        [body byte-for-byte from reconciler.py:299–537,
         dedented one level — what was inside `else:` becomes top-level
         in this function's `else:` branch]
    [the trailing `await _cleanup_build_secret(b.id)` from line 537]
```

(Important: when extracting, the dedent must be exact. Run `python -c "import ast; ast.parse(open('build_finalize.py').read())"` to verify syntax before running tests.)

- [ ] **Step 1: Create `build_finalize.py`**

Module docstring + imports + `_finalize_clean_scan` function:

```python
"""Finalize a build whose Harbor scan returned SUCCESS.

This module hosts :func:`_finalize_clean_scan`, the post-scan-SUCCESS branch
extracted from :func:`app.reconciler.builds._handle_succeeded` to keep both
files under the 15KB module-size budget. The function is structurally a
sibling helper to ``_handle_succeeded`` — same arguments + state, called
once at the end of ``_handle_succeeded`` when scan.status == SUCCESS.

Behavior is preserved 1:1 from the pre-split single-file form.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.detector import (
    Detector,
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)
from app.reconciler.log_capture import _capture_log_tail
from app.reconciler.notify import _detector_label, _ui_url, _user_context
from app.services.harbor import HarborClient
from app.services.manifest_store import ManifestDecodeError, decode_manifest_label
from app.services.notify import (
    notify_build_completed,
    notify_build_failed,
    notify_trivy_blocked,
)

logger = logging.getLogger(__name__)


async def _finalize_clean_scan(
    session: AsyncSession,
    b: DetectorBuild,
    harbor: HarborClient,
    detector: Detector,
    digest: str,
    scan,
) -> None:
    """[docstring per the template above]"""
    if scan.critical > 0:
        [bytes from reconciler.py:278–297, properly indented under `if`]
    else:
        [bytes from reconciler.py:300–536, properly indented under `else`]
    await _cleanup_build_secret(b.id)
```

Wait — `_cleanup_build_secret` lives in `builds.py` (next bullet). To avoid a circular import, the call from `build_finalize.py` to `_cleanup_build_secret` must use a deferred import OR `build_finalize.py` must own its own copy.

**Resolution**: import `_cleanup_build_secret` lazily inside `_finalize_clean_scan` (matches the existing late-import pattern in the original reconciler.py — see line 1389 `from app.models.job import Job` inside `reconcile_orphan_vcjobs` for prior art):

```python
async def _finalize_clean_scan(...):
    from app.reconciler.builds import _cleanup_build_secret
    ...
```

Or restructure: move `_cleanup_build_secret` into `build_finalize.py` (since it's only called from there + from `_handle_failed` / `_handle_timeout` in `builds.py` — but those would then need to import from `build_finalize.py`, which is also a coupling).

**Cleanest fix**: put `_cleanup_build_secret` in **`builds.py`** AND have `build_finalize.py` use a deferred import. The deferred import is one line and matches existing patterns. Don't re-organize past the size budget.

- [ ] **Step 2: Create `builds.py`**

```python
"""Build reconciliation: orchestrator + Job-state handlers.

:func:`reconcile_build` runs once per loop iteration for every DetectorBuild
in an in-flight status. The flow:

1. Read the K8s Job (``batch/v1`` Job, not Volcano — builds run on the
   build namespace's BuildKit pod).
2. Wall-clock timeout check against ``settings.BUILD_TIMEOUT_SECONDS + 60``.
3. Dispatch on K8s Job status: Succeeded → :func:`_handle_succeeded` (which
   delegates the post-scan finalization to
   :func:`app.reconciler.build_finalize._finalize_clean_scan`),
   Failed → :func:`_handle_failed`, otherwise → :func:`_update_progress`.

Helpers (``_extract_failure_reason``, ``_cleanup_build_secret``) are shared
across the three terminal paths.
"""

import asyncio
import logging
from datetime import UTC, datetime

from kubernetes.client import ApiException
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.metrics import BACKEND_ERRORS
from app.models.detector import DetectorBuild, DetectorBuildStatus
from app.reconciler.build_finalize import _finalize_clean_scan
from app.reconciler.log_capture import _capture_log_tail
from app.reconciler.notify import _detector_label, _ui_url, _user_context
from app.services.build import build_secret_name
from app.services.harbor import HarborClient, ScanStatus
from app.services.k8s import batch_v1, core_v1
from app.services.notify import notify_build_failed

logger = logging.getLogger(__name__)

IN_FLIGHT = {
    DetectorBuildStatus.PENDING,
    DetectorBuildStatus.CLONING,
    DetectorBuildStatus.VALIDATING,
    DetectorBuildStatus.BUILDING,
    DetectorBuildStatus.SCANNING,
}


async def reconcile_build(session: AsyncSession, b: DetectorBuild) -> None:
    [body byte-for-byte from reconciler.py:162–204]


async def _handle_succeeded(session: AsyncSession, b: DetectorBuild) -> None:
    [body of slim _handle_succeeded per the template above]


async def _handle_failed(session: AsyncSession, b: DetectorBuild, job) -> None:
    [body byte-for-byte from reconciler.py:540–571]


async def _handle_timeout(session: AsyncSession, b: DetectorBuild) -> None:
    [body byte-for-byte from reconciler.py:574–610]


async def _update_progress(session: AsyncSession, b: DetectorBuild, job) -> None:
    [body byte-for-byte from reconciler.py:613–630]


async def _extract_failure_reason(b: DetectorBuild) -> str:
    [body byte-for-byte from reconciler.py:730–748]


async def _cleanup_build_secret(build_id) -> None:
    [body byte-for-byte from reconciler.py:751–765]
```

- [ ] **Step 3: Verify byte budgets**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
wc -c backend/app/reconciler/builds.py backend/app/reconciler/build_finalize.py
```

Expected:

- `builds.py` ≤ 15000 bytes (~9KB)
- `build_finalize.py` ≤ 15000 bytes (~14KB)

If either exceeds 15KB, stop and reconsider. Probable cause: dedent error or a line accidentally duplicated.

- [ ] **Step 4: Verify the slim `_handle_succeeded` ⇄ `_finalize_clean_scan` byte equivalence**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
git show HEAD:backend/app/reconciler/__init__.py | sed -n '207,537p' | wc -c
cat backend/app/reconciler/builds.py backend/app/reconciler/build_finalize.py | wc -c
```

The new total bytes (≈ slim `_handle_succeeded` + `_finalize_clean_scan` + ~200 bytes of plumbing for the helper signature) must approximately equal the old `_handle_succeeded` body bytes plus a small overhead (~200 bytes). If the delta exceeds 1KB, something other than a pure extract happened.

- [ ] **Step 5: Delete moved code from `__init__.py` + add re-exports**

Delete:

- Lines 47–53 (`IN_FLIGHT` set)
- Lines 162–765 (`reconcile_build`, `_handle_succeeded`, `_handle_failed`, `_handle_timeout`, `_update_progress`, `_extract_failure_reason`, `_cleanup_build_secret` — note the log-capture functions at 633–727 already moved in Task 3, so they shouldn't be there to re-delete)

Add re-exports:

```python
from app.reconciler.builds import (
    IN_FLIGHT,
    _handle_failed,
    _handle_succeeded,
    _handle_timeout,
    reconcile_build,
)
```

`_finalize_clean_scan` does NOT need re-export (no test imports it). `_update_progress`, `_extract_failure_reason`, `_cleanup_build_secret` similarly don't need re-export.

- [ ] **Step 6: Update test patches — `test_reconciler.py`**

All 16 patch sites at lines 37, 40, 82–84, 126, 176/177, 241/242, 305/306, 386–388, 463–465, 543–545:

```python
# Before:
patch("app.reconciler.batch_v1") as bv:
patch("app.reconciler.HarborClient") as hc:
patch("app.reconciler.core_v1") as _cv,
# After:
patch("app.reconciler.builds.batch_v1") as bv:
patch("app.reconciler.builds.HarborClient") as hc:
patch("app.reconciler.builds.core_v1") as _cv,
```

But: `HarborClient` is also instantiated inside `_finalize_clean_scan`, so for tests that drive `_finalize_clean_scan` (manifest tests, CVE tests), patch BOTH:

```python
patch("app.reconciler.builds.HarborClient") as hc:
patch("app.reconciler.build_finalize.HarborClient", new=hc):  # share the mock
```

Or simpler: only patch where the lookup happens. Check each test case:

- Tests where the build's K8s Job is succeeded → hits `_handle_succeeded` → `HarborClient` lookup is in `builds.py` (from the slim `_handle_succeeded`)
- Tests where scan returns SUCCESS + critical>0 OR critical=0 → `_finalize_clean_scan` runs → but HarborClient instance is PASSED IN (fifth arg), so it's the SAME object created in `_handle_succeeded`. **Therefore patching `app.reconciler.builds.HarborClient` is sufficient** for all tests.

Verify by reading `_finalize_clean_scan`'s signature: `async def _finalize_clean_scan(session, b, harbor, detector, digest, scan)`. The `harbor` argument is passed in from `_handle_succeeded` (slim) which constructs it. So `HarborClient(...)` is only instantiated in `builds.py`. ✓

Conclusion: `app.reconciler.HarborClient` → `app.reconciler.builds.HarborClient` (single replacement).

- [ ] **Step 7: Update test patches — `test_reconciler_manifest.py`**

12 patches at lines 78–80, 155–157, 229–231, 301–303:

```python
# Before:
patch("app.reconciler.batch_v1") as bv,
patch("app.reconciler.HarborClient") as hc,
patch("app.reconciler.core_v1"),
# After:
patch("app.reconciler.builds.batch_v1") as bv,
patch("app.reconciler.builds.HarborClient") as hc,
patch("app.reconciler.builds.core_v1"),
```

- [ ] **Step 8: Update test patches — `test_reconciler_notify.py` build-side**

The full updated `_patch_notify` (lines 25–34) per the test-patch update map:

```python
@contextmanager
def _patch_notify():
    with (
        patch("app.reconciler.jobs.notify_job_completed", new=AsyncMock()) as jc,
        patch("app.reconciler.notify.notify_job_failed", new=AsyncMock()) as jf,
        patch("app.reconciler.build_finalize.notify_build_completed", new=AsyncMock()) as bc,
        patch("app.reconciler.builds.notify_build_failed", new=AsyncMock()) as bf_b,
        patch("app.reconciler.build_finalize.notify_build_failed", new=AsyncMock()) as bf_f,
        patch("app.reconciler.build_finalize.notify_trivy_blocked", new=AsyncMock()) as tb,
    ):
        yield SimpleNamespace(jc=jc, jf=jf, bc=bc, bf_b=bf_b, bf_f=bf_f, tb=tb)
```

Then update assertions inside `test_reconciler_notify.py` that previously referenced `bf` (singular) to use either `bf_b` (for `reconcile_build` 404 / `_handle_failed` / `_handle_timeout`) or `bf_f` (for fail-closed branches in `_finalize_clean_scan`). Read each `_patch_notify()` call site and pick the right one based on which test branch is exercised.

If updating assertions is too invasive (the test currently does `bf.assert_called_once_with(...)` for several cases that span both modules), add a tiny helper inside the test module:

```python
def _all_bf_calls(handles) -> list:
    """Combine call lists from both notify_build_failed mocks."""
    return handles.bf_b.call_args_list + handles.bf_f.call_args_list
```

And replace `bf.assert_called_once()` → `assert len(_all_bf_calls(handles)) == 1`. This keeps the assertion intent identical.

Lines 185, 235:

```python
# Before:
patch("app.reconciler.HarborClient", return_value=_StubHarbor()),
# After:
patch("app.reconciler.builds.HarborClient", return_value=_StubHarbor()),
```

Lines 350/351 (build path `batch_v1`/`core_v1`):

```python
# Before:
patch("app.reconciler.batch_v1", return_value=_Stub()),
patch("app.reconciler.core_v1", return_value=_Stub()),
# After:
patch("app.reconciler.builds.batch_v1", return_value=_Stub()),
patch("app.reconciler.builds.core_v1", return_value=_Stub()),
```

(Lines 407/408 already updated in Task 7 to point at `app.reconciler.jobs.*`.)

- [ ] **Step 9: Update test patches — `test_service_token_notify.py`**

Line 91:

```python
# Before:
with patch("app.reconciler.notify_job_failed", new=AsyncMock()) as m:
# After:
with patch("app.reconciler.notify.notify_job_failed", new=AsyncMock()) as m:
```

Lines 255, 256, 257:

```python
# Before:
mpatch("app.reconciler.batch_v1") as bv,
mpatch("app.reconciler.HarborClient") as hc,
mpatch("app.reconciler.notify_build_failed", new=AsyncMock()) as m,
# After:
mpatch("app.reconciler.builds.batch_v1") as bv,
mpatch("app.reconciler.builds.HarborClient") as hc,
mpatch("app.reconciler.builds.notify_build_failed", new=AsyncMock()) as m,
```

(But verify: the test at line 217 imports `reconcile_build` and at line 257 patches `notify_build_failed`. Which path does it exercise? If the test simulates a 404 from `batch_v1` → `_handle_failed` path → `notify_build_failed` lookup is in `builds.py`. Single-location patch sufficient.)

- [ ] **Step 10: Run targeted tests**

```bash
cd backend && uv run pytest tests/test_reconciler.py tests/test_reconciler_manifest.py tests/test_reconciler_notify.py tests/test_service_token_notify.py -v
```

Expected: all green.

If `test_reconciler_log_capture.py` line 208 test fails (it imports `_handle_failed, _handle_succeeded`), update its patch from the interim "patch both" form (Task 3 Step 3) to a single `app.reconciler.log_capture.core_v1` (because `_capture_log_tail` is what looks up `core_v1`, and `_handle_failed`/`_handle_succeeded` only call `_capture_log_tail` — they don't lookup `core_v1` directly anymore now that builds.py imports `_capture_log_tail` from `log_capture`).

- [ ] **Step 11: Full backend tests**

```bash
cd backend && uv run pytest -q
```

Expected: full green, baseline test count unchanged.

- [ ] **Step 12: pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/builds.py backend/app/reconciler/build_finalize.py backend/app/reconciler/__init__.py backend/tests/test_reconciler.py backend/tests/test_reconciler_manifest.py backend/tests/test_reconciler_notify.py backend/tests/test_service_token_notify.py backend/tests/test_reconciler_log_capture.py
git commit -m "refactor(reconciler): extract build reconciliation to builds.py + build_finalize.py

Splits the 22KB build section across two files to meet the <15KB budget:
- builds.py owns the orchestrator (reconcile_build, _handle_succeeded slim,
  _handle_failed, _handle_timeout, _update_progress, helpers)
- build_finalize.py owns _finalize_clean_scan, the post-scan-SUCCESS branch
  extracted from _handle_succeeded.

The Extract Function preserves behavior 1:1 — same status transitions,
DB writes, fire-and-forget notifications, secret cleanup. Verified by all
9 reconciler test files staying green at the same test count.

Test patches updated to target the new module locations per the
2026-04-30-reconciler-split.md test-patch map."
```

---

### Task 9: Extract `loop.py`

**Files:**

- Create: `backend/app/reconciler/loop.py`
- Modify: `backend/app/reconciler/__init__.py` — delete moved code, leave only `__init__.py` re-exports
- No test changes (no test imports `reconciler_loop` or the tuning constants by patch).

**Functions/constants moved (≈ 3.3KB):**

- `SYNC_EVERY_N_ITERATIONS`, `ORPHAN_SCAN_EVERY_N_ITERATIONS`, `RECONCILER_WAIT_SECONDS` (lines 156–159)
- `reconciler_loop` (lines 768–821)

- [ ] **Step 1: Create `loop.py`**

```python
"""The main reconciler loop driver.

:func:`reconciler_loop` is invoked once per backend pod from
:func:`app.main.lifespan` and runs forever (until shutdown). Each
iteration:

1. Reconciles every in-flight DetectorBuild via
   :func:`app.reconciler.builds.reconcile_build`.
2. Reconciles every non-terminal Job via
   :func:`app.reconciler.jobs.reconcile_job`.
3. Every ``SYNC_EVERY_N_ITERATIONS`` (~60s default), syncs MLflow stages via
   :func:`app.reconciler.model_sync.sync_model_versions`.
4. Every ``ORPHAN_SCAN_EVERY_N_ITERATIONS`` (~5 min default), runs orphan
   vcjob cleanup via
   :func:`app.reconciler.orphans.reconcile_orphan_vcjobs`.

Iteration failures are logged and counted to ``BACKEND_ERRORS{stage="reconciler_iteration"}``;
the loop never exits except on the supplied ``stop_event``.

The tuning constants are module-level so tests can monkeypatch them to
collapse iteration time.
"""

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.db import async_session_maker
from app.metrics import BACKEND_ERRORS
from app.models.detector import DetectorBuild
from app.models.job import NON_TERMINAL_STATUSES, Job
from app.reconciler.builds import IN_FLIGHT, reconcile_build
from app.reconciler.jobs import reconcile_job
from app.reconciler.model_sync import sync_model_versions
from app.reconciler.orphans import reconcile_orphan_vcjobs

logger = logging.getLogger(__name__)

# Loop tuning. Module-level so tests can monkeypatch to collapse iteration time.
SYNC_EVERY_N_ITERATIONS = 6
ORPHAN_SCAN_EVERY_N_ITERATIONS = 30  # ~5 min at the default 10s wait
RECONCILER_WAIT_SECONDS = 10


async def reconciler_loop(stop_event: asyncio.Event) -> None:
    [body byte-for-byte from reconciler.py:768–821]
```

- [ ] **Step 2: Delete from `__init__.py` + re-export**

Delete lines 156–159 (constants) and lines 768–821 (`reconciler_loop`). Add:

```python
from app.reconciler.loop import (
    ORPHAN_SCAN_EVERY_N_ITERATIONS,
    RECONCILER_WAIT_SECONDS,
    SYNC_EVERY_N_ITERATIONS,
    reconciler_loop,
)
```

(Constants re-exported because they're documented as "module-level so tests can monkeypatch" and existing test code may rely on `app.reconciler.SYNC_EVERY_N_ITERATIONS`.)

- [ ] **Step 3: Verify `app.main` import still works**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend python -c "from app.main import app; print('OK')"
```

Expected: `OK`. (This exercises `from app.reconciler import reconciler_loop` in `app/main.py:10`.)

- [ ] **Step 4: Full backend tests + pre-commit + commit**

```bash
cd backend && uv run pytest -q
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
pre-commit run --all-files
git add backend/app/reconciler/loop.py backend/app/reconciler/__init__.py
git commit -m "refactor(reconciler): extract reconciler_loop to loop.py"
```

---

### Task 10: Final `__init__.py` cleanup + ruff/mypy adjustments

**Files:**

- Modify: `backend/app/reconciler/__init__.py` — should now be re-exports only; clean up + add module docstring
- Modify: `ruff.toml` — remove the now-unnecessary `E402` per-file-ignore for `app/reconciler/__init__.py`
- Already done in Task 1: `mypy.ini` widened to `[mypy-app.reconciler.*]`

- [ ] **Step 1: Verify `__init__.py` contains only imports + `__all__`**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
wc -l backend/app/reconciler/__init__.py
```

Expected: ~40 lines or fewer.

The file should look like:

```python
"""Reconciler — Volcano vcjob ↔ DB job sync, build watch, orphan cleanup.

Split from a 1467-line single file in 2026-04-30; see
``docs/superpowers/plans/2026-04-30-reconciler-split.md`` for the file
structure and module responsibilities. This package's submodules:

- :mod:`app.reconciler.notify` — Discord notify helpers
- :mod:`app.reconciler.log_capture` — pod-log capture (build + job pods)
- :mod:`app.reconciler.builds` — build reconciliation orchestrator
- :mod:`app.reconciler.build_finalize` — post-scan-SUCCESS finalization
  (CVE-block + DetectorVersion promotion)
- :mod:`app.reconciler.jobs` — Volcano vcjob reconciliation
- :mod:`app.reconciler.projections` — read-model projections from job_events
- :mod:`app.reconciler.orphans` — orphan vcjob cleanup
- :mod:`app.reconciler.model_sync` — MLflow model-registry stage sync
- :mod:`app.reconciler.loop` — the main reconciler_loop driver

The names re-exported below are the public-API surface used by
``app.main`` and ``backend/tests/``. Internal helpers are intentionally
not re-exported; tests that need to patch them must reach into the
submodule (``patch("app.reconciler.<submodule>.X")``).
"""

from app.reconciler.builds import (
    IN_FLIGHT,
    _handle_failed,
    _handle_succeeded,
    _handle_timeout,
    reconcile_build,
)
from app.reconciler.jobs import (
    _handle_job_failed,
    _handle_job_succeeded,
    reconcile_job,
)
from app.reconciler.log_capture import (
    _capture_job_log_tail,
    _capture_log_tail,
    _capture_pod_logs,
    _container_from_failure_reason,
)
from app.reconciler.loop import (
    ORPHAN_SCAN_EVERY_N_ITERATIONS,
    RECONCILER_WAIT_SECONDS,
    SYNC_EVERY_N_ITERATIONS,
    reconciler_loop,
)
from app.reconciler.model_sync import sync_model_versions
from app.reconciler.notify import (
    NotifyContext,
    _detector_label,
    _fire_job_failed_notify,
    _primary_metric,
    _ui_url,
    _user_context,
)
from app.reconciler.orphans import ORPHAN_GRACE_SECONDS, reconcile_orphan_vcjobs
from app.reconciler.projections import (
    _project_prediction_summary,
    _project_summary_metrics,
)

__all__ = [
    # Public-API names referenced by app.main + tests; internal helpers omitted.
    "IN_FLIGHT",
    "ORPHAN_GRACE_SECONDS",
    "ORPHAN_SCAN_EVERY_N_ITERATIONS",
    "RECONCILER_WAIT_SECONDS",
    "SYNC_EVERY_N_ITERATIONS",
    "NotifyContext",
    "_capture_job_log_tail",
    "_capture_log_tail",
    "_capture_pod_logs",
    "_container_from_failure_reason",
    "_detector_label",
    "_fire_job_failed_notify",
    "_handle_failed",
    "_handle_job_failed",
    "_handle_job_succeeded",
    "_handle_succeeded",
    "_handle_timeout",
    "_primary_metric",
    "_project_prediction_summary",
    "_project_summary_metrics",
    "_ui_url",
    "_user_context",
    "reconcile_build",
    "reconcile_job",
    "reconcile_orphan_vcjobs",
    "reconciler_loop",
    "sync_model_versions",
]
```

- [ ] **Step 2: Remove ruff per-file-ignore for the package**

Edit `ruff.toml`:

```toml
# Delete this line:
"backend/app/reconciler/__init__.py" = ["E402"]
```

Update the comment block above it to remove the `reconciler.py` mention:

```toml
# Late imports here are an intentional code-organization pattern:
# main.py    — register users_me router AFTER Instrumentator wires the app
# E402's intent is to flag accidental late imports, which doesn't apply
# to this file. Per-file-ignore is cleaner than scattered inline noqa.
"backend/app/main.py" = ["E402"]
```

Run ruff:

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend ruff check backend/
```

Expected: `All checks passed!`. If ruff complains about E402 in `__init__.py`, the imports must already be at the top — verify and fix.

- [ ] **Step 3: Run mypy + sanity check**

```bash
uv run --project backend mypy --config-file mypy.ini
```

Expected: `Success: no issues found in 60+ source files` (the count rises by ~9 because each new submodule is its own source file). The `[mypy-app.reconciler.*]` glob covers all of them.

- [ ] **Step 4: Run the full public-API surface check**

```bash
uv run --project backend python -c "
import app.reconciler as r
expected = {
    'reconciler_loop', 'reconcile_build', 'reconcile_job', 'reconcile_orphan_vcjobs',
    '_project_summary_metrics', '_project_prediction_summary',
    '_fire_job_failed_notify', '_user_context',
    '_handle_failed', '_handle_succeeded', '_handle_job_failed', '_handle_job_succeeded',
    '_handle_timeout', '_capture_pod_logs', '_capture_log_tail',
}
missing = expected - set(dir(r))
assert not missing, f'missing names on app.reconciler: {missing}'
print('OK: all 15 public-API names present')
"
```

Expected: `OK: all 15 public-API names present`.

- [ ] **Step 5: pre-commit + commit**

```bash
pre-commit run --all-files
git add backend/app/reconciler/__init__.py ruff.toml
git commit -m "refactor(reconciler): finalize package __init__.py + remove obsolete ruff override"
```

---

### Task 11: Final verification

**No file changes** — purely a verification gate before merge.

- [ ] **Step 1: Run the full backend test suite TWICE**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split/backend
uv run pytest -q
uv run pytest -q
```

Both runs must:

- Show the exact same test count as the baseline recorded in Task 0 Step 3.
- All green. No xfail, no skipped tests not present in baseline.
- Run-to-run determinism (sometimes async tests have ordering issues — both runs must report the same numbers).

- [ ] **Step 2: Run the focused reconciler subset with -v**

```bash
cd backend && uv run pytest tests/test_reconciler.py tests/test_reconciler_jobs.py tests/test_reconciler_events.py tests/test_reconciler_log_capture.py tests/test_reconciler_manifest.py tests/test_reconciler_notify.py tests/test_reconciler_orphans.py tests/test_reconciler_prediction_summary.py tests/test_reconciler_summary_projection.py tests/test_service_token_notify.py tests/test_metrics.py -v
```

Expected: every reconciler test reported as `PASSED` individually.

- [ ] **Step 3: Verify file-size budgets**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
wc -c backend/app/reconciler/*.py | sort -n
```

Expected (rough): every file ≤ 15000 bytes. If `build_finalize.py` is 14500-15000B and `builds.py` is 8000-9000B, that matches the plan budget.

If any file exceeds 15000B, list it with the actual count and stop — propose the next reorganization step in Findings before merging.

- [ ] **Step 4: Run pre-commit one more time**

```bash
pre-commit run --all-files
```

Expected: all green.

- [ ] **Step 5: Run a clean install of dependencies + tests**

```bash
cd backend
uv sync --frozen
uv run pytest -q
```

Expected: green. (Catches any accidental dependency drift.)

- [ ] **Step 6: Verify `app.main` boots**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
uv run --project backend python -c "from app.main import app; routes = [r.path for r in app.routes]; print(f'{len(routes)} routes registered'); assert len(routes) > 30"
```

Expected: prints route count > 30 (sanity). If this fails, the package's import chain is broken even if tests pass — investigate.

- [ ] **Step 7: Helm + frontend orthogonal sanity**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
helm lint charts/lolday
cd frontend && pnpm typecheck
```

Expected: helm clean (charts unchanged), pnpm typecheck clean (frontend untouched). These should be unaffected; if either complains, it surfaces accidental cross-area changes.

- [ ] **Step 8: Push branch + open PR**

```bash
cd /home/bolin8017/Documents/repositories/lolday-reconciler-split
git push -u origin chore/reconciler-split
gh pr create --title "refactor(reconciler): split 57KB single file into 9 submodules" --body "$(cat <<'EOF'
## Summary
- Split \`backend/app/reconciler.py\` (1467 lines, 57KB) into a \`backend/app/reconciler/\` package of 9 submodules.
- Zero behavior change. All 9 reconciler test files + \`test_service_token_notify.py\` + \`test_metrics.py\` stay green at the same test count.
- One Extract Function: \`_finalize_clean_scan\` extracted from the original \`_handle_succeeded\` (332-line function, alone exceeded the 15KB budget) — flagged in the plan, behavior preserved.

Plan: [\`docs/superpowers/plans/2026-04-30-reconciler-split.md\`](docs/superpowers/plans/2026-04-30-reconciler-split.md)

Resolves [docs/architecture.md §9 #1](docs/architecture.md#9-known-tech-debt) (reconciler 57KB single file).
Does NOT resolve §9 #11 (\`[mypy-app.reconciler.*] ignore_errors = true\`) — deferred to a follow-up phase that removes the override and addresses the 20 union-attr/arg-type errors with type narrowing.

## File structure (after)

| File | Bytes | Contents |
|------|-------|----------|
| \`__init__.py\` | ~1.5KB | Public-API re-exports |
| \`notify.py\` | ~4KB | Discord notify helpers |
| \`log_capture.py\` | ~4.5KB | Pod-log capture |
| \`builds.py\` | ~9KB | Build orchestrator + handlers (slim) |
| \`build_finalize.py\` | ~14KB | Post-scan-SUCCESS finalization |
| \`jobs.py\` | ~13KB | Job orchestrator + handlers |
| \`projections.py\` | ~5.5KB | Summary + prediction projections |
| \`orphans.py\` | ~5.7KB | Orphan vcjob cleanup |
| \`model_sync.py\` | ~1.6KB | MLflow stage sync |
| \`loop.py\` | ~3.3KB | reconciler_loop driver |

## Test plan
- [x] \`uv run pytest\` (baseline count + post-split count match)
- [x] \`uv run --project backend mypy --config-file mypy.ini\` clean (override widened to package + submodules)
- [x] \`uv run ruff check backend/\` clean
- [x] \`pre-commit run --all-files\` clean
- [x] \`from app.main import app\` works (verifies main.py's \`from app.reconciler import reconciler_loop\` still resolves)
- [x] \`helm lint charts/lolday\` clean (orthogonal area unchanged)
- [x] \`pnpm typecheck\` (frontend) clean (orthogonal area unchanged)

## Follow-ups (NOT in this PR)
- Remove \`[mypy-app.reconciler.*] ignore_errors = true\` and fix the 20 union-attr/arg-type errors via type narrowing (per docs/architecture.md §9 #11 removal procedure).
- Consider extracting a shared \`_fail_build_with_notify(session, b, reason)\` helper to deduplicate the 7 fail-closed patterns in \`builds.py\` + \`build_finalize.py\`. Pure cleanup; can be its own small phase.

## Findings during execution
[Fill in any out-of-scope issues observed; prefix with "BUG:" or "TECH-DEBT:".]

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR created. Review starts.

---

## Findings

(Implementation agents fill this section in during execution. Use this template:)

### BUG / TECH-DEBT discovered (record only — not fixed in this phase)

- _none yet_

### Plan deviations

- _none yet_

---

## Execution decision tree (if something breaks)

| Symptom                                                                        | Likely cause                                                       | Action                                                                                          |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Tests fail with "AttributeError: module 'app.reconciler' has no attribute 'X'" | Forgot to re-export X from `__init__.py`                           | Add the import; re-run targeted test                                                            |
| Tests fail with "AttributeError: 'NoneType' object has no attribute ..."       | Patch target wrong; the real function ran instead of mock          | Locate where the function looks up the patched name; update to that submodule's path            |
| mypy complains about a moved file                                              | mypy override `[mypy-app.reconciler.*]` not applied                | Verify mypy.ini wildcard-glob format; re-run `uv run mypy`                                      |
| ruff complains E402 in a new submodule                                         | Late import where there shouldn't be one                           | Move the import to the top; only `__init__.py` historically had E402, and Task 10 removes that  |
| Circular import RuntimeError on `from app.main import app`                     | Submodule imports another submodule that imports it back           | Use a function-level deferred import (the pattern already in the original code; e.g. line 1389) |
| Test that was green at baseline becomes flaky/ordering-dependent               | Likely an autouse fixture's monkeypatch path didn't update         | Search for `monkeypatch.setattr("app.reconciler...` and update the path                         |
| `_finalize_clean_scan` test asserts the wrong notify mock                      | The assertion needs to use the new bf_b/bf_f split (Task 8 Step 8) | Switch to the split mocks; verify call counts sum to the original-expected total                |

---

## Done definition

- [ ] All 11 tasks committed on `chore/reconciler-split`.
- [ ] Each commit's `pytest -q` is green.
- [ ] `wc -c backend/app/reconciler/*.py` shows every file ≤ 15000B.
- [ ] `mypy.ini` has `[mypy-app.reconciler.*] ignore_errors = true` (override deliberately preserved).
- [ ] `ruff.toml`'s E402 entry for the old reconciler.py / package `__init__.py` is removed.
- [ ] `from app.reconciler import X` works for all 15 public-API names.
- [ ] `from app.main import app` works.
- [ ] `helm lint charts/lolday` and `pnpm typecheck` (frontend) clean — orthogonal areas untouched.
- [ ] PR opened with the body template above.
- [ ] `docs/architecture.md` §9 #1 flagged as resolved; #11 still open as follow-up.
