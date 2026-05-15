# Test Architecture Phase 2 — Security Boundaries & Frontend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Phase 2 of the test architecture redesign — extract `routers/jobs.py` business logic into pure services (R3), unblock multi-persona Playwright via `AUTH_DEV_PERSONAS` (R4), add the four security contract / heavy gates (cross-user MLflow ACL, CSRF token rotation, rate-limit per-user-vs-IP, audit-log durability), add JWT/JWKS contract gates, ship Kyverno + PSS enforce-mode E2E in k3d, stand up the frontend MSW + integration / visual / contract tiers, reactivate `frontend-slow.yml`, and raise vitest + Codecov coverage to `src/components/` + `src/routes/`.

**Architecture:** Phase 2 is a `Spec §10 D2.1 → D2.10` slice on top of the Phase 1 scaffolding (#193 / #194 / #195). Backend gains a service-extracted `routers/jobs.py` (the HTTP adapter shrinks below 250 lines while `services/job_validation.py` / `services/job_submission.py` / `services/job_dispatch.py` carry the testable business logic), four new security regression gates routed into the existing `contract` / `heavy` tiers, and JWKS reflector contract tests over the auth surface. Backend dev-mode auth gains a header-driven persona map so Playwright can `loginAs(page, "admin"|"developer"|"user")` against a single backend pod — the unblock condition for Phase 3 multi-persona parallel. Frontend gains four new test tiers: MSW-mocked vitest integration, playwright visual snapshots, an OpenAPI drift contract test, and the reactivated `frontend-slow.yml` running playwright against k3d. Chart-e2e flips from informational-and-red (§10.29 — k3d bundled Traefik 2.x ↔ chart's `traefik.io/v1alpha1`) to green by disabling k3d's bundled Traefik, then layering `tests/e2e_chart/` smoke tests for Kyverno-unsigned-image-rejection and PSS-restricted-privileged-rejection. Codecov gate raises to 70 % over `src/components/` + `src/routes/`. None of the new gates becomes a required check in Phase 2 (all remain informational on `backend-slow` / `frontend-slow` / `chart-e2e`) — promotion to required-check status is its own Phase-2-exit operator step.

**Tech Stack:** pytest, schemathesis, hypothesis, testcontainers-python (postgres, minio, mlflow), respx, freezegun, msw (v2), playwright (visual snapshots + persona helper), helm-unittest, k3d (with bundled Traefik disabled), kubeconform, kyverno-cli.

---

## Reference

**Source spec:** `docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md` §10 Phase 2 (D2.1 – D2.10), §9 refactors R3 + R4, §7.2 risk-class map.

**Predecessor plan:** `docs/superpowers/plans/2026-05-15-test-architecture-phase-1.md` (shipped 2026-05-16 as `745f9ec` / PR #193; admin cleanup PR #194 + skip-companion PR #195).

**Phase 1 deliverables Phase 2 builds on:**

- Layered test dirs `backend/tests/{unit,integration,contract,heavy,factories}/` — Phase 2 adds new files under the same layout.
- Root + per-layer conftests (`tests/conftest.py` < 200 lines, `integration/conftest.py`, `heavy/conftest.py`, `contract/conftest.py`).
- `MlflowClient` lifespan dependency (`backend/app/main.py` lifespan + `Depends(get_mlflow)`).
- `backend-fast.yml` / `backend-slow.yml` / `chart-e2e.yml` / `dispatch.yml` / `flaky-tracker.yml` (Phase 1 D1.6 / D1.8 / D1.11 / D1.12 / D1.13).
- Hypothesis-driven invariants on `app/models/job.py::LEGAL_TRANSITIONS` + `ResourceProfile`.
- Six helm-unittest suites (`charts/lolday/tests/`).
- Twelve anti-flaky rules (`.claude/rules/testing.md`).

Phase 3 / 4 / 5 each get their own plans, written after Phase 2 ships and is reviewed.

## Phase 1 lessons baked into this plan

These seven outcomes from Phase 1 inform task design below — captured here so the executing engineer can recognise the pattern without rereading the predecessor session.

1. **Single-task = one bite-sized commit, around 2–5 minutes of work.** Phase 1's 40-task split (`P1 T6 conftest extract`, `P1 T22 vcjob lifecycle`, `P1 T36 chart-e2e workflow`) ran fast under subagent dispatch. Phase 2 keeps the same granularity — every `D2.x` deliverable is broken into 1–5 numbered tasks. The per-D-deliverable group is labelled in each task header so the engineer can pause and review after each `D2.x` group.
2. **Contract-tier tests need a local timeout override.** `addopts = ... --timeout=30` (`backend/pyproject.toml`) is correct for the unit / integration tier; schemathesis fuzz against a real ASGI app needs **180 s**. Every Phase 2 contract test below carries `pytestmark = [pytest.mark.contract, pytest.mark.timeout(180)]` — same pattern as the Phase 1 D1.7 contract files.
3. **`kubernetes-fake-client` is fictional.** The package does not exist on PyPI. Use the in-house `mock_k8s_batch` autouse fixture in `backend/tests/integration/conftest.py` (Volcano CRD via `kubernetes.client.CustomObjectsApi`) plus the `_patched_k8s` context-manager pattern from `tests/integration/reconciler/test_reconciler_jobs.py` for full-lifecycle scenarios. No external dep needed; do not run `uv add kubernetes-fake-client`.
4. **Alembic `disable_existing_loggers=True` silently disables application loggers.** Phase 1 centralised the fix in `backend/tests/integration/services/conftest.py` `_reenable_app_loggers` autouse fixture. New service-layer integration tests inherit it automatically — _do not_ re-implement the fix per-test. If a Phase 2 test lives under `tests/heavy/` or `tests/contract/` and exercises a logger, copy the autouse fixture pattern into the matching layer conftest (don't move it up to root, which would re-enable all loggers everywhere).
5. **Helm-unittest fixtures encode the chart's** **default** **values.** `charts/lolday/tests/kyverno_policy_test.yaml` asserts the Harbor ClusterPolicy renders `validationFailureAction: Audit` (the chart default), and `pss_test.yaml` asserts `lolday-jobs` renders `enforce=baseline`. Phase 2 D2.5 tests **runtime** behaviour against k3d _after_ an operator `kubectl patch` flips to Enforce / restricted — it does **not** change the chart defaults. Do not modify the helm-unittest fixtures unless the chart itself changes.
6. **Branch protection is now strict over 9 required contexts.** Adding a new required check requires:
   (a) a workflow with `name: <foo>` and `jobs.<bar>: name: <bar>` so the GitHub API sees the raw `check_run.name` as `<bar>` (not `<foo> / <bar>`);
   (b) a matching `.github/workflows/<name>-skip.yml` companion if the workflow has a `paths:` filter (otherwise docs-only PRs stall on "Expected — waiting");
   (c) a `gh api PUT /repos/.../branches/main/protection` call with the full body (the sub-resource PUT 404s when bootstrapping).
   **Phase 2 deliberately leaves every new test gate informational.** None of D2.3 / D2.4 / D2.5 / D2.6 / D2.7 / D2.8 / D2.9 / D2.10 promotes to a required check during Phase 2 — that decision is a separate operator step after the gates stabilise, owned by the Phase 2 exit verification task (Task 29).
7. **`chart-e2e.yml` is currently red.** k3d v5.7.5 bundles Traefik v2 by default; the lolday chart writes `IngressRoute` against `traefik.io/v1alpha1` (Traefik v3). When `helm install lolday` runs on k3d, the bundled Traefik admission rejects the chart's IngressRoute CRDs (`traefik.io/v1alpha1` not registered by Traefik v2). This blocks D2.5 (k3d + helm install + Kyverno verification). **Root-cause fix** — pass `--k3s-arg "--disable=traefik@server:*"` to `k3d cluster create`, dropping the bundled Traefik so our chart's Traefik settings take over cleanly. Task 15 lands this fix to `chart-e2e.yml` before D2.5 introduces additional e2e shell tests; the helm-unittest fixtures stay unchanged because the chart's Traefik settings are unchanged.

---

## Prerequisites (small PRs that may land in parallel with this plan)

The Phase-1 ship-state assumes these are already in place:

- [x] **D1.6 / D1.7 / D1.8 / D1.11 / D1.12 / D1.13** — backend split, contract + heavy tier, chart-e2e workflow, paths-filter dispatch, flaky-tracker. Shipped in `745f9ec`.
- [x] **Branch protection** with 9 required contexts (`pre-commit`, `pytest`, `unit`, `lint-template`, `build-image (backend)`, `build-image (frontend)`, `build-helper (build-helper)`, `build-helper (job-helper)`, `gitleaks`). Verified by `gh api repos/bolin8017/lolday/branches/main/protection/required_status_checks`.
- [x] Five `<name>-skip.yml` skip-companions for the path-filtered required checks.
- [x] `.claude/rules/testing.md` with twelve anti-flaky rules.

If any of the above is missing or red, **stop** and resolve before starting Phase 2 — every Phase 2 task assumes the layered tier exists and the CI gates are wired.

The pending tech-debt items from `project_test_architecture_phase_1_shipped` auto-memory:

- Harbor Kyverno ClusterPolicy is `Audit`, not `Enforce` — **Phase 2 D2.5 keeps the chart at `Audit` and exercises the `Enforce` ramp via `kubectl patch` in the new e2e shell test**, matching the operator runbook (`docs/runbooks/kyverno-harbor-signing.md`). The chart-side audit-→-enforce values flag (`kyverno.harborImageSignatureEnforce`) is out-of-scope for this plan (separate operator decision, issue #187, 2026-05-22).
- `lolday-jobs` namespace is `enforce=baseline`, not `restricted` — same pattern: **D2.5 leaves the chart at `baseline` and exercises `restricted` via `kubectl label --overwrite` in the e2e shell test**. The chart-side promotion is out-of-scope for this plan (operator runbook `docs/runbooks/pss-label-promotion.md`, issue #186, 2026-05-18).
- `GET /api/v1/users/me` OpenAPI is missing `422` response entry — **Phase 2 D2.3 Task 8 adjoins the OpenAPI fix to the cross-user MLflow contract test work** (single PR cycle; the change is `responses={422: ErrorResponse}` on the `read_me` route). The Phase-1 `xfail` on `test_schemathesis_users_me.py` lifts in the same task.

---

## File Structure

**New files**

Backend services + tests:

- `backend/app/services/job_validation.py` (R3 part 1)
- `backend/app/services/job_submission.py` (R3 part 2)
- `backend/app/services/job_dispatch.py` (R3 part 3 — supersedes today's `jobs_dispatch.py`; new file kept under singular name per spec §9 R3)
- `backend/tests/unit/services/test_job_validation.py`
- `backend/tests/unit/services/test_job_submission.py`
- `backend/tests/unit/services/test_job_dispatch.py`
- `backend/tests/contract/openapi/test_mlflow_authz_cross_user.py` (D2.3)
- `backend/tests/heavy/mlflow/test_acl_real_multi_user.py` (D2.3)
- `backend/tests/integration/routers/test_csrf_token_rotation.py` (D2.3)
- `backend/tests/integration/routers/test_rate_limit_user_vs_ip.py` (D2.3)
- `backend/tests/heavy/postgres/test_audit_log_durability.py` (D2.3)
- `backend/tests/heavy/auth/__init__.py`
- `backend/tests/heavy/auth/test_jwks_reflector.py` (D2.4)
- `backend/tests/integration/services/test_jwks_cache_ttl.py` (D2.4)

Chart + e2e shell tests:

- `tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh` (D2.5)
- `tests/e2e_chart/test_pss_enforce_privileged.sh` (D2.5)

Frontend tests + mocks:

- `frontend/tests/mocks/handlers.ts` (D2.6)
- `frontend/tests/mocks/server.ts` (D2.6)
- `frontend/tests/mocks/setup.ts` (D2.6)
- `frontend/tests/integration/routes/jobs.test.tsx` (D2.6)
- `frontend/tests/integration/routes/detectors.test.tsx` (D2.6)
- `frontend/tests/integration/forms/JobSubmitForm.flow.test.tsx` (D2.6)
- `frontend/tests/visual/rjsf_form_snapshots.spec.ts` (D2.7)
- `frontend/tests/visual/sidebar_snapshots.spec.ts` (D2.7)
- `frontend/tests/visual/page_header_snapshots.spec.ts` (D2.7)
- `frontend/tests/contract/schema_gen_drift.test.ts` (D2.8)
- `.github/workflows/frontend-slow.yml` (D2.9)

**Modified files**

- `backend/app/routers/jobs.py` — HTTP-adapter slim-down per R3 (≤ 250 lines target; current 916)
- `backend/app/config.py` — `AUTH_DEV_PERSONAS` dict; `AUTH_DEV_EMAIL` deprecation note (R4)
- `backend/app/auth/cf_access.py` — `resolve_user_from_jwt` reads `X-Dev-Persona` header before falling back to `AUTH_DEV_EMAIL` (R4)
- `backend/app/routers/users_me.py` — add `422` to OpenAPI responses (Phase 1 tech debt closure)
- `backend/tests/contract/openapi/test_schemathesis_users_me.py` — lift xfail for `422` case
- `backend/tests/integration/services/conftest.py` — no change expected (Phase 1 `_reenable_app_loggers` autouse continues to apply)
- `.github/workflows/chart-e2e.yml` — drop k3d-bundled Traefik (`--k3s-arg "--disable=traefik@server:*"`); fixes §10.29 ahead of D2.5
- `frontend/tests/e2e/helpers.ts` — `loginAs(page, role)` helper (R4)
- `frontend/vitest.config.ts` — `coverage.include` extends to `src/components/**` + `src/routes/**`; `coverage.thresholds.lines = 70`; MSW global setup
- `frontend/package.json` — adds `msw` (v2.x) and `@axe-core/playwright` to devDependencies (axe used in Phase 3 D3.6 — kept out of P2; only msw added now)
- `.codecov.yml` — frontend project target raised to 70 %

**Deleted files**

- `backend/app/services/jobs_dispatch.py` (renamed → `job_dispatch.py` via Task 4; `git mv` preserves history)

---

## Tasks

### Task 1: Create `app/services/job_validation.py` (D2.1 / R3 — part 1 of 5)

**Files:**

- Create: `backend/app/services/job_validation.py`
- Create: `backend/tests/unit/services/test_job_validation.py`

- [ ] **Step 1: Read the existing `routers/jobs.py` POST /jobs validation logic**

Run: `awk 'NR>=80 && NR<=320' backend/app/routers/jobs.py` (the POST `/jobs` handler today inlines DB lookup, dataset integrity, user-params validate, idempotency-key compute). The new module owns the **pure** part — no `session.add` / no K8s — so future contract / hypothesis tests can call it without `TestClient`.

- [ ] **Step 2: Write the failing test**

Open `backend/tests/unit/services/test_job_validation.py`:

```python
"""Unit tests for the pure validate_submission service (D2.1 / R3)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatasetConfig, Detector, DetectorVersion, User
from app.models.job import JobType, ResourceProfile
from app.schemas.job import JobCreate
from app.services.job_validation import (
    ValidatedJob,
    ValidationError,
    validate_submission,
)


pytestmark = pytest.mark.asyncio


async def test_validate_submission_happy_path(
    session: AsyncSession, seeded_train_job_inputs
) -> None:
    user, detector, dversion, dataset = seeded_train_job_inputs
    payload = JobCreate(
        type=JobType.TRAIN,
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        resource_profile=ResourceProfile.GPU1,
        train_params={"epochs": 3},
    )

    validated = await validate_submission(session, user, payload)

    assert isinstance(validated, ValidatedJob)
    assert validated.detector.id == detector.id
    assert validated.idempotency_key  # 32-hex sha
    assert validated.normalised_params == {"epochs": 3}


async def test_validate_submission_unknown_detector_raises_422(
    session: AsyncSession, test_user
) -> None:
    payload = JobCreate(
        type=JobType.TRAIN,
        detector_id=uuid.uuid4(),
        detector_version_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        resource_profile=ResourceProfile.GPU1,
        train_params={},
    )

    with pytest.raises(ValidationError) as exc:
        await validate_submission(session, test_user, payload)

    assert exc.value.code == "detector_not_found"


async def test_validate_submission_predict_without_model_raises(
    session: AsyncSession, seeded_predict_job_inputs
) -> None:
    user, detector, dversion, dataset = seeded_predict_job_inputs
    payload = JobCreate(
        type=JobType.PREDICT,
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        resource_profile=ResourceProfile.STANDARD,
        train_params=None,
        model_version_id=None,
    )

    with pytest.raises(ValidationError) as exc:
        await validate_submission(session, user, payload)

    assert exc.value.code == "model_required_for_predict"
```

Add the two seed fixtures in `backend/tests/unit/services/conftest.py` (create if absent — short helpers around the polyfactory `job_factory` already established in Phase 1):

```python
import pytest

from tests.factories.user_factory import UserFactory
from tests.factories.detector_factory import DetectorFactory, DetectorVersionFactory
from tests.factories.dataset_factory import DatasetFactory


@pytest.fixture
async def test_user(session):
    user = UserFactory.build()
    session.add(user)
    await session.flush()
    return user


@pytest.fixture
async def seeded_train_job_inputs(session, test_user):
    detector = DetectorFactory.build(owner=test_user)
    dversion = DetectorVersionFactory.build(detector=detector)
    dataset = DatasetFactory.build(owner=test_user)
    session.add_all([detector, dversion, dataset])
    await session.flush()
    return test_user, detector, dversion, dataset


@pytest.fixture
async def seeded_predict_job_inputs(session, test_user):
    # same shape as train inputs — predict-stage check happens in validate_submission
    return await anext(seeded_train_job_inputs.__wrapped__(session, test_user))
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend
uv run pytest tests/unit/services/test_job_validation.py -v
```

Expected: `ImportError: cannot import name 'validate_submission' from 'app.services.job_validation'`.

- [ ] **Step 4: Write the minimal `job_validation.py` implementation**

Create `backend/app/services/job_validation.py`:

```python
"""Pure-function payload validation for the job-submission flow.

Extracted from ``app.routers.jobs.POST /jobs`` so the validation can be
exercised by hypothesis (`unit/invariants/`) and schemathesis
(`contract/openapi/`) without spinning up a TestClient.

Inputs are the request-scoped AsyncSession and the authenticated User plus
the parsed Pydantic ``JobCreate`` body. Outputs are a frozen
``ValidatedJob`` dataclass; mutations (``session.add`` / commit) happen in
``app.services.job_submission.submit_job`` (Task 2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatasetConfig, Detector, DetectorVersion, ModelVersion, User
from app.models.job import JobType, ResourceProfile
from app.schemas.job import JobCreate
from app.services.jobs_params_validate import (
    UserParamsRejected,
    resolve_detector_defaults,
    validate_user_params,
)


class ValidationError(Exception):
    """Raised when the submitted payload cannot be turned into a Job row."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedJob:
    job_type: JobType
    detector: Detector
    detector_version: DetectorVersion
    dataset: DatasetConfig
    model_version: ModelVersion | None
    normalised_params: dict
    resource_profile: ResourceProfile
    idempotency_key: str


async def validate_submission(
    session: AsyncSession,
    user: User,
    payload: JobCreate,
) -> ValidatedJob:
    detector = await session.get(Detector, payload.detector_id)
    if detector is None or detector.deleted_at is not None:
        raise ValidationError("detector_not_found", "detector does not exist")
    if not _user_can_use_detector(user, detector):
        raise ValidationError("detector_forbidden", "no access to detector")

    dversion = await session.get(DetectorVersion, payload.detector_version_id)
    if dversion is None or dversion.detector_id != detector.id:
        raise ValidationError(
            "detector_version_mismatch",
            "detector_version does not belong to detector",
        )

    dataset = await session.get(DatasetConfig, payload.dataset_id)
    if dataset is None or dataset.deleted_at is not None:
        raise ValidationError("dataset_not_found", "dataset does not exist")

    model_version: ModelVersion | None = None
    if payload.type == JobType.PREDICT:
        if payload.model_version_id is None:
            raise ValidationError(
                "model_required_for_predict",
                "predict jobs require model_version_id",
            )
        model_version = await session.get(ModelVersion, payload.model_version_id)
        if model_version is None:
            raise ValidationError("model_not_found", "model_version does not exist")

    try:
        defaults = resolve_detector_defaults(dversion, payload.type)
        normalised = validate_user_params(payload.train_params or {}, dversion, payload.type, defaults)
    except UserParamsRejected as e:
        raise ValidationError("user_params_invalid", str(e)) from e

    idem_payload = {
        "type": payload.type.value,
        "detector_version_id": str(dversion.id),
        "dataset_id": str(dataset.id),
        "model_version_id": str(model_version.id) if model_version else None,
        "resource_profile": payload.resource_profile.value if isinstance(payload.resource_profile, ResourceProfile) else payload.resource_profile,
        "params": normalised,
    }
    idempotency_key = hashlib.sha256(
        json.dumps(idem_payload, sort_keys=True).encode()
    ).hexdigest()

    return ValidatedJob(
        job_type=payload.type,
        detector=detector,
        detector_version=dversion,
        dataset=dataset,
        model_version=model_version,
        normalised_params=normalised,
        resource_profile=payload.resource_profile if isinstance(payload.resource_profile, ResourceProfile) else ResourceProfile(payload.resource_profile),
        idempotency_key=idempotency_key,
    )


def _user_can_use_detector(user: User, detector: Detector) -> bool:
    return detector.owner_id == user.id or detector.visibility.value == "public"
```

- [ ] **Step 5: Run the test — expect green**

```bash
cd backend
uv run pytest tests/unit/services/test_job_validation.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/job_validation.py \
        backend/tests/unit/services/test_job_validation.py \
        backend/tests/unit/services/conftest.py
git commit -m "feat(backend/services): extract job_validation.py from routers/jobs.py (D2.1 / R3 part 1)"
```

---

### Task 2: Create `app/services/job_submission.py` (D2.1 / R3 — part 2 of 5)

**Files:**

- Create: `backend/app/services/job_submission.py`
- Create: `backend/tests/unit/services/test_job_submission.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the submit_job service (D2.1 / R3 part 2)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.schemas.job import JobCreate
from app.services.job_submission import IdempotencyConflict, submit_job
from app.services.job_validation import validate_submission


pytestmark = pytest.mark.asyncio


async def test_submit_job_inserts_queued_backend_row(
    session: AsyncSession, seeded_train_job_inputs
) -> None:
    user, detector, dversion, dataset = seeded_train_job_inputs
    payload = JobCreate(
        type="train",
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        resource_profile="gpu1",
        train_params={"epochs": 3},
    )
    validated = await validate_submission(session, user, payload)

    job = await submit_job(session, user, validated)
    await session.flush()

    assert job.status == JobStatus.QUEUED_BACKEND
    assert job.submitted_by_id == user.id
    assert job.idempotency_key == validated.idempotency_key


async def test_submit_job_idempotent_replay_returns_existing(
    session: AsyncSession, seeded_train_job_inputs
) -> None:
    user, detector, dversion, dataset = seeded_train_job_inputs
    payload = JobCreate(
        type="train",
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        resource_profile="gpu1",
        train_params={"epochs": 3},
    )
    validated = await validate_submission(session, user, payload)

    first = await submit_job(session, user, validated)
    await session.flush()
    second = await submit_job(session, user, validated)

    assert second.id == first.id

    count = await session.scalar(
        select(Job).where(Job.idempotency_key == validated.idempotency_key)
    )
    assert count is not None


async def test_submit_job_distinct_user_same_key_is_conflict(
    session: AsyncSession, seeded_train_job_inputs, other_user
) -> None:
    user, detector, dversion, dataset = seeded_train_job_inputs
    payload = JobCreate(
        type="train",
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        resource_profile="gpu1",
        train_params={"epochs": 3},
    )
    validated_a = await validate_submission(session, user, payload)
    await submit_job(session, user, validated_a)
    await session.flush()

    validated_b = await validate_submission(session, other_user, payload)
    with pytest.raises(IdempotencyConflict):
        await submit_job(session, other_user, validated_b)
```

Add `other_user` fixture to `backend/tests/unit/services/conftest.py`:

```python
@pytest.fixture
async def other_user(session):
    user = UserFactory.build()
    session.add(user)
    await session.flush()
    return user
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
cd backend
uv run pytest tests/unit/services/test_job_submission.py -v
```

Expected: `ImportError: cannot import name 'submit_job' from 'app.services.job_submission'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/job_submission.py`:

```python
"""Pure orchestration: turn a ValidatedJob into a persisted Job row.

Splits the second half of ``POST /jobs`` out of ``routers/jobs.py``. The
caller (router or reconciler) owns the transaction boundary; this function
only ``add()``s and returns the row.

Idempotency:
- A repeat call by the same user with the same payload returns the existing
  Job (same idempotency_key, same submitted_by_id).
- A call by a different user with the same payload raises IdempotencyConflict
  (the key is global per design; cross-user replay would leak access).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.job import Job, JobStatus
from app.services.job_validation import ValidatedJob


class IdempotencyConflict(Exception):
    pass


async def submit_job(
    session: AsyncSession,
    user: User,
    validated: ValidatedJob,
) -> Job:
    existing = await session.scalar(
        select(Job).where(Job.idempotency_key == validated.idempotency_key)
    )
    if existing is not None:
        if existing.submitted_by_id != user.id:
            raise IdempotencyConflict(
                f"idempotency_key {validated.idempotency_key} already used by another user"
            )
        return existing

    job = Job(
        id=uuid.uuid4(),
        type=validated.job_type,
        submitted_by_id=user.id,
        detector_id=validated.detector.id,
        detector_version_id=validated.detector_version.id,
        dataset_id=validated.dataset.id,
        model_version_id=validated.model_version.id if validated.model_version else None,
        resource_profile=validated.resource_profile,
        normalised_params=validated.normalised_params,
        idempotency_key=validated.idempotency_key,
        status=JobStatus.QUEUED_BACKEND,
        submitted_at=datetime.now(UTC),
    )
    session.add(job)
    return job
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd backend
uv run pytest tests/unit/services/test_job_submission.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/job_submission.py \
        backend/tests/unit/services/test_job_submission.py \
        backend/tests/unit/services/conftest.py
git commit -m "feat(backend/services): extract job_submission.py from routers/jobs.py (D2.1 / R3 part 2)"
```

---

### Task 3: Create `app/services/job_dispatch.py` (D2.1 / R3 — part 3 of 5)

**Files:**

- Create: `backend/app/services/job_dispatch.py` (via `git mv` of existing `jobs_dispatch.py`)
- Modify: every importer of `app.services.jobs_dispatch` switches to `app.services.job_dispatch`
- Create: `backend/tests/unit/services/test_job_dispatch.py`

- [ ] **Step 1: `git mv` the existing file**

```bash
cd backend/app/services
git mv jobs_dispatch.py job_dispatch.py
```

- [ ] **Step 2: Update import sites**

```bash
cd backend
grep -rln "from app.services.jobs_dispatch" app/ tests/
```

For each match, edit to `from app.services.job_dispatch` (also fix bare `app.services.jobs_dispatch` strings, e.g. mocker `patch()` targets).

Likely call sites: `app/routers/jobs.py`, `app/reconciler/fifo_scheduler.py`, `app/reconciler/jobs.py`, plus the matching `tests/integration/reconciler/*.py` files. Verify with grep before commit.

- [ ] **Step 3: Run full backend test suite — expect green**

```bash
cd backend
uv run pytest -q -m "not heavy"
```

Expected: all existing 843 fast-tier tests still pass (rename is import-only).

- [ ] **Step 4: Write the failing unit test for the new pure-function entry**

The R3 spec calls for `dispatch_to_volcano(job, k8s_client) -> None`. The existing module already exposes the right shape (`dispatch_to_volcano(session, job)` — see `backend/app/services/job_dispatch.py:90`). Confirm the public function signature; if it differs, surface a thin wrapper. Add `backend/tests/unit/services/test_job_dispatch.py`:

```python
"""Unit tests for dispatch_to_volcano (D2.1 / R3 part 3)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.services.job_dispatch import dispatch_to_volcano


pytestmark = pytest.mark.asyncio


async def test_dispatch_transitions_to_preparing(
    session: AsyncSession,
    queued_backend_job: Job,
    mock_k8s_batch,  # autouse fixture in integration/conftest.py
) -> None:
    await dispatch_to_volcano(session, queued_backend_job)
    await session.flush()

    assert queued_backend_job.status == JobStatus.PREPARING
    assert queued_backend_job.token_hash is not None


async def test_dispatch_idempotent_replay_does_not_double_create(
    session: AsyncSession,
    queued_backend_job: Job,
    mock_k8s_batch,
) -> None:
    await dispatch_to_volcano(session, queued_backend_job)
    await session.flush()

    # Replay: K8s create returns 409, function tolerates and leaves status
    await dispatch_to_volcano(session, queued_backend_job)
    await session.flush()

    assert queued_backend_job.status == JobStatus.PREPARING
```

Add `queued_backend_job` fixture (in `backend/tests/unit/services/conftest.py`):

```python
@pytest.fixture
async def queued_backend_job(session, seeded_train_job_inputs):
    user, detector, dversion, dataset = seeded_train_job_inputs
    from app.models.job import Job, JobStatus, JobType
    import uuid
    from datetime import UTC, datetime

    job = Job(
        id=uuid.uuid4(),
        type=JobType.TRAIN,
        submitted_by_id=user.id,
        detector_id=detector.id,
        detector_version_id=dversion.id,
        dataset_id=dataset.id,
        normalised_params={"epochs": 1},
        idempotency_key="deadbeef" * 8,
        status=JobStatus.QUEUED_BACKEND,
        submitted_at=datetime.now(UTC),
    )
    session.add(job)
    await session.flush()
    return job
```

- [ ] **Step 5: Run — expect pass (because the rename preserved the existing function)**

```bash
cd backend
uv run pytest tests/unit/services/test_job_dispatch.py -v
```

Expected: 2 passed. If the existing function name differs, add a thin wrapper inside `job_dispatch.py`:

```python
async def dispatch_to_volcano(session: AsyncSession, job: Job) -> None:
    """Public Phase-2 entry; delegates to the underlying existing function."""
    return await _existing_dispatch_implementation(session, job)
```

- [ ] **Step 6: Commit**

```bash
git add -A backend/app/services/ backend/app/routers/jobs.py \
        backend/app/reconciler/ backend/tests/
git commit -m "feat(backend/services): rename jobs_dispatch.py -> job_dispatch.py + unit test (D2.1 / R3 part 3)"
```

---

### Task 4: Migrate `routers/jobs.py::create_job` to use the new services (D2.1 / R3 — part 4 of 5)

**Files:**

- Modify: `backend/app/routers/jobs.py` (POST `/jobs` handler only)

- [ ] **Step 1: Find the existing POST `/jobs` handler**

```bash
cd backend
grep -n '@router.post("/")' app/routers/jobs.py
```

Identify the handler function (typically `create_job` or `submit`).

- [ ] **Step 2: Rewrite the handler to use the three pure services**

Replace the body of the POST `/jobs` handler with:

```python
from app.services.job_validation import ValidationError, validate_submission
from app.services.job_submission import IdempotencyConflict, submit_job
from app.services.job_dispatch import dispatch_to_volcano

@router.post(
    "",
    response_model=JobRead,
    status_code=201,
    responses={
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def create_job(
    payload: JobCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(require_role(Role.DEVELOPER)),
    _rate: None = Depends(rate_limit_user),
) -> JobRead:
    try:
        validated = await validate_submission(session, user, payload)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail={"code": e.code, "message": e.message})

    try:
        job = await submit_job(session, user, validated)
    except IdempotencyConflict as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(job)
    await dispatch_to_volcano(session, job)
    await session.commit()
    await session.refresh(job)

    return JobRead.model_validate(job)
```

Adjust the response model and the `require_role` enum / `rate_limit_user` import to match what already exists in the file.

- [ ] **Step 3: Run the existing router integration tests**

```bash
cd backend
uv run pytest tests/integration/routers/test_jobs.py tests/integration/routers/test_routers_jobs.py -v
```

Expected: green. The integration tier still exercises the full endpoint; the new pure services are tested separately in unit.

- [ ] **Step 4: Run full fast tier**

```bash
cd backend
uv run pytest -q -m "not heavy"
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/jobs.py
git commit -m "refactor(backend/routers): POST /jobs uses validate_submission + submit_job + dispatch_to_volcano (D2.1 / R3 part 4)"
```

---

### Task 5: Slim `routers/jobs.py` to ≤ 250 lines (D2.1 / R3 — part 5 of 5)

**Files:**

- Modify: `backend/app/routers/jobs.py`

- [ ] **Step 1: Inventory remaining inline logic**

```bash
cd backend
wc -l app/routers/jobs.py
```

After Task 4 the file should be smaller but still > 250 lines (helpers, GET / PATCH / WS handlers etc.). Identify lines that are NOT thin HTTP-adapter glue — typically:

- `_load_dataset` helper that wraps `session.get(DatasetConfig, …)` plus a 422 raise — leave it (it's HTTP-shaped error mapping).
- Inline owner-ref construction (lines around 350–430) — extract into `job_dispatch._owner_ref_from_job(job)` if not already there.
- Inline rate-limit metric increment — leave (HTTP-shaped).

Target: anything that no longer has `request`/`HTTPException` references should move to a service module. **Do not** force the file below 250 lines for its own sake — the goal is "HTTP adapter only". 250 is an aspiration in the spec; 350 is acceptable.

- [ ] **Step 2: Move owner-ref helper into `job_dispatch.py`**

If `routers/jobs.py` still computes the K8s OwnerReference for the vcjob (`metadata.ownerReferences = [{kind: Job, name: …}]`), extract:

```python
# backend/app/services/job_dispatch.py
def vcjob_owner_ref(job: Job) -> dict:
    return {
        "apiVersion": "lolday.io/v1",
        "kind": "Job",
        "name": str(job.id),
        "uid": str(job.id),  # placeholder; real owner ref linkage is via DB FK, not K8s GC
        "controller": True,
    }
```

Replace the inline copy in the router.

- [ ] **Step 3: Verify the router still type-checks**

```bash
cd backend
uv run mypy app/routers/jobs.py
```

Expected: no errors.

- [ ] **Step 4: Run full fast tier**

```bash
cd backend
uv run pytest -q -m "not heavy"
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/jobs.py backend/app/services/job_dispatch.py
git commit -m "refactor(backend/routers): slim routers/jobs.py — owner-ref helper into job_dispatch (D2.1 / R3 part 5)"
```

---

### Task 6: `AUTH_DEV_PERSONAS` config + persona-aware `resolve_user_from_jwt` (D2.2 / R4 — part 1 of 2)

**Files:**

- Modify: `backend/app/config.py`
- Modify: `backend/app/auth/cf_access.py`
- Create: `backend/tests/integration/services/test_auth_dev_personas.py`

- [ ] **Step 1: Write the failing integration test**

`backend/tests/integration/services/test_auth_dev_personas.py`:

```python
"""Integration test for AUTH_DEV_PERSONAS multi-persona dev mode (D2.2 / R4)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_x_dev_persona_admin_returns_admin_role(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/users/me",
        headers={"X-Dev-Persona": "admin"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert r.json()["email"] == "admin@dev.local"


async def test_x_dev_persona_developer_returns_developer_role(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/users/me",
        headers={"X-Dev-Persona": "developer"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "developer"
    assert r.json()["email"] == "dev@dev.local"


async def test_x_dev_persona_user_returns_user_role(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/users/me",
        headers={"X-Dev-Persona": "user"},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "user"
    assert r.json()["email"] == "user@dev.local"


async def test_x_dev_persona_unknown_returns_401(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/users/me",
        headers={"X-Dev-Persona": "ghost"},
    )
    assert r.status_code == 401
    assert "unknown persona" in r.json()["detail"].lower()


async def test_no_x_dev_persona_falls_back_to_auth_dev_email(
    app_client: AsyncClient,
) -> None:
    # No X-Dev-Persona header; backward-compat single-persona path.
    r = await app_client.get("/api/v1/users/me")
    assert r.status_code == 200
    # Equals whatever AUTH_DEV_EMAIL is set to in the test settings.
```

`app_client` is the existing fixture from `backend/tests/integration/conftest.py`; it boots the app with `AUTH_DEV_MODE=true` + `AUTH_DEV_EMAIL=admin@example.com`.

- [ ] **Step 2: Run the test — expect 5 failures**

```bash
cd backend
uv run pytest tests/integration/services/test_auth_dev_personas.py -v
```

Expected: all 5 fail (header is ignored today; backend always returns the `AUTH_DEV_EMAIL` user).

- [ ] **Step 3: Extend `app/config.py`**

Locate the existing `AUTH_DEV_MODE` / `AUTH_DEV_EMAIL` declaration around line 126:

```python
AUTH_DEV_MODE: bool = False
AUTH_DEV_EMAIL: str = ""

AUTH_DEV_PERSONAS: dict[str, dict[str, str]] = {
    "admin": {"email": "admin@dev.local", "role": "admin"},
    "developer": {"email": "dev@dev.local", "role": "developer"},
    "user": {"email": "user@dev.local", "role": "user"},
}
```

If `Settings` is a pydantic `BaseSettings`, the dict default is fine; it picks up the literal. Add a brief `# noqa: B008  # default dict literal is intentional for dev mode` if ruff complains.

- [ ] **Step 4: Wire the header in `auth/cf_access.py`**

Locate `resolve_user_from_jwt` (currently around line 202–293). Update the dev-mode branch:

```python
async def resolve_user_from_jwt(
    session: AsyncSession,
    token: str | None,
    *,
    log_context: str = "",
    persona_header: str | None = None,
) -> User:
    if settings.AUTH_DEV_MODE:
        if persona_header:
            persona = settings.AUTH_DEV_PERSONAS.get(persona_header)
            if persona is None:
                raise CfAccessAuthError(
                    f"unknown persona {persona_header!r}; known: {list(settings.AUTH_DEV_PERSONAS)}"
                )
            user = await get_or_create_user_by_email(session, persona["email"])
            user.role = Role(persona["role"])  # mutate in-session; commit handled by caller
            await session.flush()
            return user
        if not settings.AUTH_DEV_EMAIL:
            raise CfAccessAuthError("AUTH_DEV_MODE enabled but AUTH_DEV_EMAIL empty")
        return await get_or_create_user_by_email(session, settings.AUTH_DEV_EMAIL)
    # ... (existing JWT verification path unchanged)
```

Pass the header through `cf_access_user`:

```python
async def cf_access_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    token = request.headers.get("cf-access-jwt-assertion")
    persona = request.headers.get("x-dev-persona") if settings.AUTH_DEV_MODE else None
    ...
    try:
        return await resolve_user_from_jwt(
            session, token, log_context=f"path={request.url.path}", persona_header=persona
        )
    except CfAccessAuthError as e:
        raise HTTPException(401, str(e)) from e
```

- [ ] **Step 5: Run the test — expect 5 passed**

```bash
cd backend
uv run pytest tests/integration/services/test_auth_dev_personas.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the full fast tier**

```bash
cd backend
uv run pytest -q -m "not heavy"
```

Expected: green. (Existing tests don't set `X-Dev-Persona`, so the fallback branch keeps them working.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py backend/app/auth/cf_access.py \
        backend/tests/integration/services/test_auth_dev_personas.py
git commit -m "feat(backend/auth): AUTH_DEV_PERSONAS multi-persona dev mode via X-Dev-Persona header (D2.2 / R4 part 1)"
```

---

### Task 7: Frontend `loginAs(page, role)` helper (D2.2 / R4 — part 2 of 2)

**Files:**

- Modify: `frontend/tests/e2e/helpers.ts`

- [ ] **Step 1: Add the helper export**

Open `frontend/tests/e2e/helpers.ts` and append:

```typescript
import type { Page } from "@playwright/test";

export type DevPersona = "admin" | "developer" | "user";

/**
 * D2.2 / R4 — switch the backend's dev-mode persona for the next request.
 *
 * The backend reads `X-Dev-Persona` once per request when `AUTH_DEV_MODE=true`.
 * We install a route handler that injects the header onto every request to
 * `/api/v1/*` so navigation, API calls, and WebSocket upgrades all carry it.
 */
export async function loginAs(page: Page, role: DevPersona): Promise<void> {
  await page.context().setExtraHTTPHeaders({ "X-Dev-Persona": role });
  // Reload the page to ensure the next render reads the new persona's `/users/me`.
  if (page.url() && page.url() !== "about:blank") {
    await page.reload();
  }
}
```

- [ ] **Step 2: Verify the existing single-persona E2E specs still compile**

```bash
cd frontend
pnpm playwright test --list 2>&1 | tail -5
```

Expected: no compile error. (No spec uses `loginAs` yet — those are Phase 3 D3.2.)

- [ ] **Step 3: Sanity-check the header round-trip in a tiny manual spec (kept ephemeral)**

Create `frontend/tests/e2e/_persona_smoke.spec.ts` (delete after Phase 2 ships):

```typescript
import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers";

test("loginAs admin returns admin role on /users/me", async ({ page }) => {
  await loginAs(page, "admin");
  const resp = await page.request.get("/api/v1/users/me");
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.role).toBe("admin");
});
```

Run against a live local backend (`AUTH_DEV_MODE=true`):

```bash
cd frontend
pnpm playwright test _persona_smoke.spec.ts --project=chromium
```

Expected: pass. Delete the smoke spec:

```bash
git rm frontend/tests/e2e/_persona_smoke.spec.ts
```

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/e2e/helpers.ts
git commit -m "feat(frontend/e2e): loginAs(page, role) helper for AUTH_DEV_PERSONAS (D2.2 / R4 part 2)"
```

---

### Task 8: Cross-user MLflow ACL contract test + `/users/me` 422 fix (D2.3 — part 1 of 5)

**Files:**

- Create: `backend/tests/contract/openapi/test_mlflow_authz_cross_user.py`
- Modify: `backend/app/routers/users_me.py`
- Modify: `backend/tests/contract/openapi/test_schemathesis_users_me.py`

- [ ] **Step 1: Fix the missing `422` response on `GET /users/me`**

Open `backend/app/routers/users_me.py`, locate the `read_me` route, and add the response:

```python
from app.schemas.errors import ErrorResponse

@router.get(
    "/me",
    response_model=UserRead,
    responses={
        200: {"model": UserRead},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},  # NEW — closes phase-1 schemathesis xfail
    },
)
async def read_me(...) -> UserRead:
    ...
```

If `ErrorResponse` does not yet exist as a Pydantic model, add to `backend/app/schemas/errors.py`:

```python
from pydantic import BaseModel

class ErrorResponse(BaseModel):
    detail: str
```

- [ ] **Step 2: Lift the xfail in the existing schemathesis test**

Open `backend/tests/contract/openapi/test_schemathesis_users_me.py` and remove the xfail decorator / inline comment referencing the missing 422 response. The exact line numbers depend on Phase 1 output — search for `xfail` or `422` and prune.

- [ ] **Step 3: Run the existing users_me schemathesis test**

```bash
cd backend
uv run pytest tests/contract/openapi/test_schemathesis_users_me.py -v
```

Expected: green (no more xfail; the schema now declares 422 and schemathesis no longer flags it).

- [ ] **Step 4: Write the cross-user MLflow ACL contract test**

Create `backend/tests/contract/openapi/test_mlflow_authz_cross_user.py`:

```python
"""D2.3 — Contract test: cross-user MLflow ACL.

User A creates an experiment; user B must receive 403 (not 404, not 200)
on every read / write endpoint that touches A's experiment_id.

This is schemathesis-driven so adding a new MLflow-proxy endpoint
automatically gets covered.
"""

from __future__ import annotations

import pytest
import schemathesis
from httpx import AsyncClient

pytestmark = [pytest.mark.contract, pytest.mark.timeout(180)]


schema = schemathesis.from_pytest_fixture("schema").include(
    path_regex=r"^/api/v1/experiments-proxy/.*"
)


@pytest.fixture
async def user_a_experiment_id(app_client_as_admin: AsyncClient) -> str:
    """Seed an experiment owned by user A. Returns the experiment_id."""
    r = await app_client_as_admin.post(
        "/api/v1/experiments-proxy/api/2.0/mlflow/experiments/create",
        json={"name": "user_a_exp_acl"},
    )
    assert r.status_code == 200
    return r.json()["experiment_id"]


@schema.parametrize()
async def test_user_b_cannot_read_user_a_experiment(
    case,
    user_a_experiment_id,
    app_client_as_user_b: AsyncClient,
) -> None:
    """User B's request must 403 (or 200 with an empty page) on any
    endpoint where user_a_experiment_id is the path/query/body subject.
    User B never owns user A's experiments."""
    if user_a_experiment_id in str(case.body) or user_a_experiment_id in str(case.query):
        response = await case.call_asgi(app_client_as_user_b.app)
        assert response.status_code in {403, 404}, (
            f"cross-user read leaked: status={response.status_code} body={response.text[:200]}"
        )
```

Add `app_client_as_admin` and `app_client_as_user_b` fixtures to `backend/tests/contract/conftest.py`:

```python
@pytest.fixture
async def app_client_as_admin(app_client):
    app_client.headers.update({"X-Dev-Persona": "admin"})
    yield app_client
    app_client.headers.pop("X-Dev-Persona", None)


@pytest.fixture
async def app_client_as_user_b(app_client):
    app_client.headers.update({"X-Dev-Persona": "user"})
    yield app_client
    app_client.headers.pop("X-Dev-Persona", None)
```

(These reuse the R4 multi-persona work from Task 6. The `app_client` fixture is the schemathesis-aware ASGI client from Phase 1 `contract/conftest.py`.)

- [ ] **Step 5: Run the contract test**

```bash
cd backend
uv run pytest tests/contract/openapi/test_mlflow_authz_cross_user.py -v --timeout=180
```

Expected: pass for all generated cases (the existing `_mlflow_user_filter` in `experiments_proxy.py` enforces the ACL). If any case 200s with user A's data leaked, that's a real security regression — file an issue before merging this test.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/users_me.py \
        backend/app/schemas/errors.py \
        backend/tests/contract/openapi/test_schemathesis_users_me.py \
        backend/tests/contract/openapi/test_mlflow_authz_cross_user.py \
        backend/tests/contract/conftest.py
git commit -m "feat(backend/tests): cross-user MLflow ACL contract test + close /users/me 422 OpenAPI gap (D2.3 part 1)"
```

---

### Task 9: Cross-user MLflow ACL heavy tier on real MLflow (D2.3 — part 2 of 5)

**Files:**

- Create: `backend/tests/heavy/mlflow/test_acl_real_multi_user.py`

- [ ] **Step 1: Write the heavy-tier test**

```python
"""D2.3 — Heavy tier: cross-user MLflow ACL against a real MLflow container.

The contract tier (Task 8) catches schema-level leaks; this test catches
implementation drift where the in-memory MLflow stub diverges from the
actual REST API behaviour. Uses the session-scoped `mlflow_container`
fixture from heavy/conftest.py (Phase 1 D1.8).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.heavy, pytest.mark.no_mock_mlflow]


async def test_user_b_get_experiment_returns_403_on_real_mlflow(
    app_client_factory,
    mlflow_container,
) -> None:
    admin_client: AsyncClient = await app_client_factory(persona="admin")
    user_client: AsyncClient = await app_client_factory(persona="user")

    # User A (admin) creates an experiment.
    r = await admin_client.post(
        "/api/v1/experiments-proxy/api/2.0/mlflow/experiments/create",
        json={"name": "user_a_real_mlflow"},
    )
    assert r.status_code == 200
    exp_id = r.json()["experiment_id"]

    # User B attempts to read it.
    r = await user_client.get(
        f"/api/v1/experiments-proxy/api/2.0/mlflow/experiments/get?experiment_id={exp_id}"
    )
    assert r.status_code in {403, 404}, r.text


async def test_user_b_search_runs_filters_out_user_a_runs(
    app_client_factory,
    mlflow_container,
) -> None:
    admin_client = await app_client_factory(persona="admin")
    user_client = await app_client_factory(persona="user")

    # Admin creates an experiment + a run.
    r = await admin_client.post(
        "/api/v1/experiments-proxy/api/2.0/mlflow/experiments/create",
        json={"name": "user_a_search_test"},
    )
    exp_id = r.json()["experiment_id"]
    r = await admin_client.post(
        "/api/v1/experiments-proxy/api/2.0/mlflow/runs/create",
        json={"experiment_id": exp_id, "start_time": 0},
    )
    assert r.status_code == 200

    # User B searches — must return zero hits.
    r = await user_client.post(
        "/api/v1/experiments-proxy/api/2.0/mlflow/runs/search",
        json={"experiment_ids": [exp_id]},
    )
    assert r.status_code == 200
    assert r.json().get("runs", []) == []
```

`app_client_factory` is a new fixture in `backend/tests/heavy/conftest.py` (add it):

```python
@pytest.fixture
async def app_client_factory(app, mlflow_container):
    """Persona-aware AsyncClient factory for heavy tier."""
    from httpx import ASGITransport, AsyncClient

    async def _factory(*, persona: str) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Dev-Persona": persona},
        )
        return client

    return _factory
```

- [ ] **Step 2: Run heavy tier locally (Docker required)**

```bash
cd backend
uv run pytest tests/heavy/mlflow/test_acl_real_multi_user.py -v -m heavy --timeout=120
```

Expected: 2 passed. The first run pulls the MLflow image (~30 s); subsequent runs reuse the session-scoped container.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/heavy/mlflow/test_acl_real_multi_user.py \
        backend/tests/heavy/conftest.py
git commit -m "feat(backend/tests): heavy tier — cross-user MLflow ACL on real MLflow (D2.3 part 2)"
```

---

### Task 10: CSRF token rotation full flow (D2.3 — part 3 of 5)

**Files:**

- Create: `backend/tests/integration/routers/test_csrf_token_rotation.py`

- [ ] **Step 1: Write the test**

The CSRF middleware in `app/middleware/csrf.py` rejects state-changing requests without a `Sec-Fetch-Site: same-origin` or matching `Origin` header. There's no explicit "token" in this implementation — the test exercises the rotation pattern that happens implicitly on origin-changing reloads (Set-Cookie / cookie invalidation paths). Test covers:

1. POST with valid `Origin` matching `Host` — accepted.
2. POST with `Origin` mismatch — 403.
3. POST after `Origin` changes (e.g. session continued across deploy) — still rejected.
4. POST with `Sec-Fetch-Site: same-origin` and no `Origin` — accepted.
5. POST with no CSRF-relevant header (CLI / service-token path via `/api/v1/internal/*`) — accepted (the exempt prefix).

```python
"""D2.3 — Integration test: CSRF token rotation / origin enforcement flow."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "host,origin,expect_status",
    [
        ("lolday.connlabai.com", "https://lolday.connlabai.com", 422),  # accepted; 422 is the schema error for empty body
        ("lolday.connlabai.com", "https://evil.example.com",   403),  # rejected
        ("lolday.connlabai.com", "",                          422),  # no Origin but Sec-Fetch-Site below
    ],
)
async def test_csrf_origin_check(
    app_client: AsyncClient,
    host: str,
    origin: str,
    expect_status: int,
) -> None:
    headers = {"Host": host}
    if origin:
        headers["Origin"] = origin
    else:
        headers["Sec-Fetch-Site"] = "same-origin"
    r = await app_client.post("/api/v1/jobs", json={}, headers=headers)
    assert r.status_code == expect_status, r.text


async def test_csrf_exempt_internal_path_does_not_check(
    app_client: AsyncClient,
) -> None:
    r = await app_client.post(
        "/api/v1/internal/events/heartbeat",
        json={},
        headers={"Host": "lolday.connlabai.com", "Origin": "https://evil.example.com"},
    )
    # The /internal/* path is exempt; the response code is whatever the
    # handler returns (likely 401 from job-token auth, or 422 from schema).
    assert r.status_code != 403, "internal path should not 403 on Origin mismatch"


async def test_csrf_get_request_never_blocked(
    app_client: AsyncClient,
) -> None:
    r = await app_client.get(
        "/api/v1/users/me",
        headers={"Host": "lolday.connlabai.com", "Origin": "https://evil.example.com"},
    )
    assert r.status_code != 403  # GET methods bypass CSRF middleware entirely
```

- [ ] **Step 2: Run**

```bash
cd backend
uv run pytest tests/integration/routers/test_csrf_token_rotation.py -v
```

Expected: all parametrize cases plus the two named tests pass. If the cross-origin case returns 422 instead of 403, the CSRF middleware order is wrong (ensure it runs **before** request-body parsing) — fix in `app/main.py`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/routers/test_csrf_token_rotation.py
git commit -m "feat(backend/tests): CSRF token-rotation / origin enforcement integration test (D2.3 part 3)"
```

---

### Task 11: Rate-limit per-user vs per-IP (D2.3 — part 4 of 5)

**Files:**

- Create: `backend/tests/integration/routers/test_rate_limit_user_vs_ip.py`

- [ ] **Step 1: Write the test**

`backend/app/services/rate_limit.py` exposes both `rate_limit_ip` (login route) and `rate_limit_user` (POST /jobs, POST /detectors/{id}/builds). The bug class to catch: a route accidentally swaps the wrong limiter and an IP-shared NAT crashes everyone.

```python
"""D2.3 — Rate-limit boundary: per-user vs per-IP keying.

Two users on the same NAT IP must not share a per-user bucket; one user on
two IPs must share a per-user bucket. fakeredis (from integration/conftest)
provides the storage; the test asserts the keying invariant directly.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


JOB_LIMIT_PER_MIN = 6  # mirror `rate_limit_user` default; adjust to settings if changed


async def _submit_minimal_job(client: AsyncClient, *, persona: str) -> int:
    """Submit a syntactically minimal job, return status code."""
    r = await client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_id": "00000000-0000-0000-0000-000000000001",
            "detector_version_id": "00000000-0000-0000-0000-000000000002",
            "dataset_id": "00000000-0000-0000-0000-000000000003",
            "resource_profile": "standard",
            "train_params": {"epochs": 1},
        },
        headers={"X-Dev-Persona": persona, "X-Forwarded-For": "10.0.0.42"},
    )
    return r.status_code


async def test_per_user_bucket_isolates_two_users_on_same_ip(
    app_client_admin: AsyncClient,
    app_client_developer: AsyncClient,
) -> None:
    """Admin exhausts; developer (different user, same IP) still has their own bucket.

    Both personas are >= DEVELOPER so both pass require_role(DEVELOPER) and
    reach rate_limit_user. If the limiter were IP-keyed the developer's
    second request would 429 — that would be the bug this test catches.
    """
    # Admin saturates.
    for _ in range(JOB_LIMIT_PER_MIN):
        await _submit_minimal_job(app_client_admin, persona="admin")
    code = await _submit_minimal_job(app_client_admin, persona="admin")
    assert code == 429

    # Developer on same IP still allowed.
    code = await _submit_minimal_job(app_client_developer, persona="developer")
    assert code != 429, "developer bucket bled into admin's IP-scoped exhaustion"


async def test_per_user_bucket_shared_across_two_ips_for_same_user(
    app_client_admin: AsyncClient,
) -> None:
    """Admin from IP A exhausts; admin from IP B is also exhausted."""
    for _ in range(JOB_LIMIT_PER_MIN):
        r = await app_client_admin.post(
            "/api/v1/jobs",
            json={"type": "train", "detector_id": "00000000-0000-0000-0000-000000000001",
                  "detector_version_id": "00000000-0000-0000-0000-000000000002",
                  "dataset_id": "00000000-0000-0000-0000-000000000003",
                  "resource_profile": "standard", "train_params": {}},
            headers={"X-Dev-Persona": "admin", "X-Forwarded-For": "10.0.0.42"},
        )
    r = await app_client_admin.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_id": "00000000-0000-0000-0000-000000000001",
              "detector_version_id": "00000000-0000-0000-0000-000000000002",
              "dataset_id": "00000000-0000-0000-0000-000000000003",
              "resource_profile": "standard", "train_params": {}},
        headers={"X-Dev-Persona": "admin", "X-Forwarded-For": "10.0.0.99"},
    )
    assert r.status_code == 429, "admin bucket should be IP-independent"
```

Add fixtures to `backend/tests/integration/conftest.py` if absent:

```python
@pytest.fixture
async def app_client_admin(app_client):
    app_client.headers.update({"X-Dev-Persona": "admin"})
    yield app_client
    app_client.headers.pop("X-Dev-Persona", None)


@pytest.fixture
async def app_client_developer(app_client):
    app_client.headers.update({"X-Dev-Persona": "developer"})
    yield app_client
    app_client.headers.pop("X-Dev-Persona", None)


@pytest.fixture
async def app_client_user(app_client):
    """Persona below DEVELOPER role — for negative-auth / forbidden tests."""
    app_client.headers.update({"X-Dev-Persona": "user"})
    yield app_client
    app_client.headers.pop("X-Dev-Persona", None)
```

- [ ] **Step 2: Run**

```bash
cd backend
uv run pytest tests/integration/routers/test_rate_limit_user_vs_ip.py -v
```

Expected: 2 passed. If the second test fails with `code != 429` it means `rate_limit_user` is keyed on IP — that's a real bug; fix `app/services/rate_limit.py` to key on `user.id` only.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/routers/test_rate_limit_user_vs_ip.py \
        backend/tests/integration/conftest.py
git commit -m "feat(backend/tests): rate-limit per-user vs per-IP keying integration test (D2.3 part 4)"
```

---

### Task 12: Audit-log durability on real Postgres (D2.3 — part 5 of 5)

**Files:**

- Create: `backend/tests/heavy/postgres/test_audit_log_durability.py`

- [ ] **Step 1: Write the test**

`services/audit.py` writes to the `audit_log` table via a session-scoped append; the test exercises **real PG** behaviour to catch aiosqlite-vs-asyncpg divergence (JSON / generated-column / unique-index drift).

```python
"""D2.3 — Heavy tier: audit_log writes survive crash + reconnect on real PG.

aiosqlite (used by the integration tier) does not exercise the same WAL /
fsync / connection-pool behaviour as asyncpg + Postgres. This test uses
the session-scoped `pg_container` fixture (Phase 1 heavy/conftest.py) to
verify durability invariants.
"""

from __future__ import annotations

import pytest

from app.services.audit import emit_audit
from app.models.audit_log import AuditLog
from sqlalchemy import select

pytestmark = pytest.mark.heavy


async def test_audit_log_row_persists_after_session_close(pg_session_factory) -> None:
    async with pg_session_factory() as session:
        await emit_audit(
            session,
            actor_id="00000000-0000-0000-0000-000000000001",
            action="dataset.visibility.changed",
            target_id="00000000-0000-0000-0000-000000000002",
            metadata={"from": "private", "to": "public"},
        )
        await session.commit()

    # New session, new connection.
    async with pg_session_factory() as session2:
        rows = (await session2.scalars(select(AuditLog))).all()
        assert any(r.action == "dataset.visibility.changed" for r in rows)


async def test_audit_log_concurrent_writes_do_not_collide(pg_session_factory) -> None:
    import asyncio

    async def _write_one(i: int):
        async with pg_session_factory() as session:
            await emit_audit(
                session,
                actor_id=f"00000000-0000-0000-0000-{i:012d}",
                action="test.concurrent",
                target_id=None,
                metadata={"seq": i},
            )
            await session.commit()

    await asyncio.gather(*[_write_one(i) for i in range(10)])

    async with pg_session_factory() as session:
        rows = (await session.scalars(
            select(AuditLog).where(AuditLog.action == "test.concurrent")
        )).all()
        assert len(rows) == 10
```

The `pg_session_factory` is provided by `backend/tests/heavy/conftest.py` (Phase 1 D1.8); confirm by `grep pg_session_factory backend/tests/heavy/conftest.py`. If absent, add it as a thin wrapper around the testcontainers `pg_container.get_connection_url()` + `create_async_engine(...)` pair already present.

- [ ] **Step 2: Run**

```bash
cd backend
uv run pytest tests/heavy/postgres/test_audit_log_durability.py -v -m heavy --timeout=120
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/heavy/postgres/test_audit_log_durability.py
git commit -m "feat(backend/tests): heavy tier — audit_log durability on real Postgres (D2.3 part 5)"
```

---

### Task 13: JWKS reflector heavy-tier auth test (D2.4 — part 1 of 2)

**Files:**

- Create: `backend/tests/heavy/auth/__init__.py`
- Create: `backend/tests/heavy/auth/conftest.py`
- Create: `backend/tests/heavy/auth/test_jwks_reflector.py`

- [ ] **Step 1: `__init__.py` + reflector fixture**

Create `backend/tests/heavy/auth/__init__.py` (empty).

Create `backend/tests/heavy/auth/conftest.py`:

```python
"""Heavy-tier conftest for the auth surface.

Spins up a tiny FastAPI app on a random port that serves /.well-known/jwks
backed by a freshly-generated RSA key. The lolday backend points
JWKS_URL at this reflector for the duration of the test, so the cf_access
JWT path exercises real network + signature verification.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import jwt as pyjwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI


@pytest.fixture(scope="session")
def jwks_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    return private_key, public_key


@pytest.fixture(scope="session")
async def jwks_server(jwks_keypair):
    private_key, public_key = jwks_keypair
    pub_pem = public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    app = FastAPI()

    @app.get("/.well-known/jwks")
    async def jwks():
        # Minimal JWKS payload — single RSA key
        from jwt.algorithms import RSAAlgorithm
        jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
        jwk["kid"] = "test-kid"
        return {"keys": [jwk]}

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/.well-known/jwks"
    yield url
    server.should_exit = True
    await task


@pytest.fixture
def mint_cf_jwt(jwks_keypair):
    """Factory fixture: returns a callable that mints a CF-Access-shaped JWT
    signed by the reflector's RSA key. Lets tests parametrise claims without
    a module-level helper import (which would race with pytest's conftest
    discovery order)."""
    private_key, _ = jwks_keypair

    def _mint(*, sub: str, aud: str, iss: str, email: str | None = None, **extra) -> str:
        headers = {"kid": "test-kid"}
        claims = {"sub": sub, "aud": aud, "iss": iss, **extra}
        if email is not None:
            claims["email"] = email
        return pyjwt.encode(claims, private_key, algorithm="RS256", headers=headers)

    return _mint
```

- [ ] **Step 2: Write the failing test**

`backend/tests/heavy/auth/test_jwks_reflector.py`:

```python
"""D2.4 — Heavy tier: full JWT verification path against a real JWKS server."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.heavy


async def test_valid_jwt_passes_full_verification(
    app_client_factory, jwks_server, mint_cf_jwt, monkeypatch
):
    monkeypatch.setenv("JWKS_URL", jwks_server)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")

    token = mint_cf_jwt(
        sub="user-1",
        aud="lolday-test-aud",
        iss="https://lolday-test-team.cloudflareaccess.com",
        email="real-jwt@example.com",
    )
    client: AsyncClient = await app_client_factory()
    r = await client.get(
        "/api/v1/users/me",
        headers={"cf-access-jwt-assertion": token},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "real-jwt@example.com"


async def test_jwt_with_wrong_audience_returns_401(
    app_client_factory, jwks_server, mint_cf_jwt, monkeypatch
):
    monkeypatch.setenv("JWKS_URL", jwks_server)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")

    token = mint_cf_jwt(
        sub="user-1",
        aud="WRONG-AUD",
        iss="https://lolday-test-team.cloudflareaccess.com",
        email="real-jwt@example.com",
    )
    client: AsyncClient = await app_client_factory()
    r = await client.get(
        "/api/v1/users/me",
        headers={"cf-access-jwt-assertion": token},
    )
    assert r.status_code == 401


async def test_malformed_jwt_returns_401(
    app_client_factory, jwks_server, monkeypatch
):
    monkeypatch.setenv("JWKS_URL", jwks_server)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")

    client: AsyncClient = await app_client_factory()
    r = await client.get(
        "/api/v1/users/me",
        headers={"cf-access-jwt-assertion": "not.a.jwt"},
    )
    assert r.status_code == 401
```

- [ ] **Step 3: Run**

```bash
cd backend
uv run pytest tests/heavy/auth/test_jwks_reflector.py -v -m heavy --timeout=120
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/heavy/auth/
git commit -m "feat(backend/tests): heavy tier — JWT verification against JWKS reflector (D2.4 part 1)"
```

---

### Task 14: JWKS cache TTL with freezegun (D2.4 — part 2 of 2)

**Files:**

- Create: `backend/tests/integration/services/test_jwks_cache_ttl.py`

- [ ] **Step 1: Write the test**

```python
"""D2.4 — JWKS cache TTL respect: PyJWKClient is wrapped in a singleton
in app/auth/cf_access.py with `cache_keys=True` and `lifespan=600`.
freezegun lets us assert the cache evicts at the boundary instead of
relying on real time.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from app.auth.cf_access import _get_jwks_client


pytestmark = pytest.mark.asyncio


async def test_jwks_client_caches_across_calls_within_lifespan() -> None:
    client = _get_jwks_client()

    with patch.object(client, "fetch_data", return_value={"keys": []}) as fetch:
        with freeze_time("2026-05-16 10:00:00"):
            client.get_jwk_set()
            assert fetch.call_count == 1
            client.get_jwk_set()
            assert fetch.call_count == 1  # cached

        # Move time forward by less than the lifespan (default 600 s).
        with freeze_time("2026-05-16 10:09:00"):
            client.get_jwk_set()
            assert fetch.call_count == 1  # still cached


async def test_jwks_client_evicts_after_lifespan_elapses() -> None:
    client = _get_jwks_client()

    with patch.object(client, "fetch_data", return_value={"keys": []}) as fetch:
        with freeze_time("2026-05-16 10:00:00"):
            client.get_jwk_set()
            assert fetch.call_count == 1

        with freeze_time("2026-05-16 10:11:00"):  # +11 min > 10-min lifespan
            client.get_jwk_set()
            assert fetch.call_count == 2  # cache evicted
```

- [ ] **Step 2: Run**

```bash
cd backend
uv run pytest tests/integration/services/test_jwks_cache_ttl.py -v
```

Expected: 2 passed. If `_get_jwks_client` does not return a cache-enabled `PyJWKClient`, this surfaces it — fix `app/auth/cf_access.py` to pass `cache_keys=True, lifespan=600` (the mainstream PyJWT default).

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/services/test_jwks_cache_ttl.py
git commit -m "feat(backend/tests): JWKS cache TTL boundary test with freezegun (D2.4 part 2)"
```

---

### Task 15: Drop k3d bundled Traefik to unblock `chart-e2e.yml` (D2.5 — prerequisite + part 1 of 3)

**Files:**

- Modify: `.github/workflows/chart-e2e.yml`

This task closes `docs/architecture.md` §10.29 (Phase 1 lesson #7). Without it, `chart-e2e.yml` cannot reach the helm-install step, so D2.5's e2e shell tests can't run.

- [ ] **Step 1: Inspect the current `k3d cluster create` line**

```bash
grep -n "k3d cluster create" .github/workflows/chart-e2e.yml
```

- [ ] **Step 2: Patch to disable bundled Traefik**

Replace the `k3d cluster create` invocation with:

```yaml
- name: Create k3d cluster
  run: |
    k3d cluster create lolday-e2e \
      --servers 1 --agents 0 \
      --k3s-arg "--disable=traefik@server:*" \
      --k3s-arg "--disable=servicelb@server:*" \
      --wait
```

The two `--disable` arguments drop the bundled Traefik (we install our own Traefik via the chart) and bundled servicelb (our chart provisions its own LoadBalancer-equivalent via host port-forwarding in e2e). The `@server:*` suffix scopes the disable to all server nodes (mainstream k3s syntax).

- [ ] **Step 3: Manual local k3d sanity (optional but recommended)**

```bash
k3d cluster create lolday-e2e-local \
  --servers 1 --agents 0 \
  --k3s-arg "--disable=traefik@server:*" \
  --k3s-arg "--disable=servicelb@server:*" \
  --wait

kubectl get crd | grep traefik   # should print nothing — Traefik 2.x bundled CRDs absent
helm install lolday charts/lolday -f charts/lolday/values-test.yaml
kubectl rollout status -n lolday deploy/lolday-backend --timeout=120s
k3d cluster delete lolday-e2e-local
```

Expected: `helm install` succeeds; backend Deployment reaches Ready. If `helm install` errors on a chart Traefik CRD, the chart needs to apply CRDs explicitly before the IngressRoute objects — typically by setting `traefik.enabled: true` in `values-test.yaml`.

- [ ] **Step 4: Push and observe the workflow run on a draft PR**

```bash
git checkout -b chore/chart-e2e-disable-bundled-traefik
git add .github/workflows/chart-e2e.yml
git commit -m "fix(ci): chart-e2e disables k3d bundled Traefik + servicelb (closes architecture.md §10.29)"
git push -u origin chore/chart-e2e-disable-bundled-traefik
gh pr create --draft --title "fix(ci): chart-e2e disables k3d bundled Traefik + servicelb" \
  --body "Phase 2 prerequisite — Task 15 of the Phase 2 plan. Closes architecture.md §10.29."
gh pr checks --watch
```

Expected: `chart-e2e / deploy-smoke` turns green. (Promotion to required-check is _not_ part of this task — that's a separate operator step.)

- [ ] **Step 5: Merge after green and continue**

After CI green, return to the main Phase 2 branch and proceed.

---

### Task 16: e2e shell test — `test_kyverno_unsigned_image_rejected.sh` (D2.5 — part 2 of 3)

**Files:**

- Create: `tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh`
- Modify: `.github/workflows/chart-e2e.yml` (invoke the new test)

- [ ] **Step 1: Write the shell test**

```bash
#!/usr/bin/env bash
# D2.5 — Verify Kyverno rejects an unsigned Harbor image when the policy is
# in Enforce mode. The chart ships the policy at Audit; this test patches
# to Enforce, applies a Pod referencing an unsigned image, and asserts
# admission is rejected.
#
# Run from repo root: bash tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh
set -euo pipefail

NS=${TEST_NS:-default}

trap '
  kubectl -n kyverno patch clusterpolicy verify-lolday-harbor-image-signatures \
    --type=json -p='"'"'[{"op":"replace","path":"/spec/validationFailureAction","value":"Audit"}]'"'"' || true
  kubectl delete pod -n "${NS}" e2e-unsigned-image-test --ignore-not-found
' EXIT

echo "::group::Flip Harbor signature policy to Enforce"
kubectl -n kyverno patch clusterpolicy verify-lolday-harbor-image-signatures \
  --type=json -p='[{"op":"replace","path":"/spec/validationFailureAction","value":"Enforce"}]'
kubectl -n kyverno get clusterpolicy verify-lolday-harbor-image-signatures \
  -o jsonpath='{.spec.validationFailureAction}' | grep -q Enforce
echo "::endgroup::"

echo "::group::Apply a Pod with an unsigned Harbor image (expect rejection)"
set +e
kubectl -n "${NS}" apply -f - <<'YAML' 2>&1 | tee /tmp/kyverno-apply.log
apiVersion: v1
kind: Pod
metadata:
  name: e2e-unsigned-image-test
spec:
  containers:
    - name: c
      image: harbor.lolday.svc:80/lolday/nonexistent-unsigned:latest
  restartPolicy: Never
YAML
rc=$?
set -e
echo "::endgroup::"

if [ "$rc" -eq 0 ]; then
  echo "::error::Pod admission succeeded with an unsigned image; expected rejection"
  exit 1
fi

if ! grep -q 'verify-lolday-harbor-image-signatures' /tmp/kyverno-apply.log; then
  echo "::error::Pod was rejected but not by Kyverno verify-lolday-harbor-image-signatures policy"
  cat /tmp/kyverno-apply.log
  exit 1
fi

echo "PASS: Kyverno rejected the unsigned image as expected"
```

Make it executable:

```bash
chmod +x tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh
```

- [ ] **Step 2: Wire into `chart-e2e.yml`**

Add a step after the existing `helm install` / wait-for-ready block:

```yaml
- name: D2.5 — Kyverno rejects unsigned Harbor image
  run: bash tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh
```

- [ ] **Step 3: Local sanity**

If you have a local k3d cluster from Task 15 with the chart installed:

```bash
bash tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh
```

Expected: `PASS: Kyverno rejected the unsigned image as expected`.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e_chart/test_kyverno_unsigned_image_rejected.sh \
        .github/workflows/chart-e2e.yml
git commit -m "feat(tests/e2e_chart): Kyverno unsigned-image rejection e2e shell test (D2.5 part 2)"
```

---

### Task 17: e2e shell test — `test_pss_enforce_privileged.sh` (D2.5 — part 3 of 3)

**Files:**

- Create: `tests/e2e_chart/test_pss_enforce_privileged.sh`
- Modify: `.github/workflows/chart-e2e.yml`

- [ ] **Step 1: Write the shell test**

```bash
#!/usr/bin/env bash
# D2.5 — Verify PSS rejects a privileged Pod when the lolday-jobs namespace
# is labelled enforce=restricted. The chart ships lolday-jobs at baseline;
# this test patches the label to restricted, applies a privileged Pod, and
# asserts admission is rejected.
set -euo pipefail

NS=lolday-jobs
trap '
  kubectl label namespace "${NS}" \
    pod-security.kubernetes.io/enforce=baseline --overwrite || true
  kubectl delete pod -n "${NS}" e2e-pss-privileged-test --ignore-not-found
' EXIT

echo "::group::Promote ${NS} to enforce=restricted"
kubectl label namespace "${NS}" \
  pod-security.kubernetes.io/enforce=restricted --overwrite
kubectl get namespace "${NS}" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}' \
  | grep -q restricted
echo "::endgroup::"

echo "::group::Apply a privileged Pod (expect rejection)"
set +e
kubectl -n "${NS}" apply -f - <<'YAML' 2>&1 | tee /tmp/pss-apply.log
apiVersion: v1
kind: Pod
metadata:
  name: e2e-pss-privileged-test
spec:
  containers:
    - name: c
      image: alpine:3.20
      securityContext:
        privileged: true
  restartPolicy: Never
YAML
rc=$?
set -e
echo "::endgroup::"

if [ "$rc" -eq 0 ]; then
  echo "::error::Privileged Pod admission succeeded under enforce=restricted"
  exit 1
fi

if ! grep -q 'violates PodSecurity "restricted' /tmp/pss-apply.log; then
  echo "::error::Pod was rejected but not by PSS restricted profile"
  cat /tmp/pss-apply.log
  exit 1
fi

echo "PASS: PSS restricted profile rejected the privileged Pod as expected"
```

```bash
chmod +x tests/e2e_chart/test_pss_enforce_privileged.sh
```

- [ ] **Step 2: Wire into `chart-e2e.yml`**

```yaml
- name: D2.5 — PSS rejects privileged Pod under enforce=restricted
  run: bash tests/e2e_chart/test_pss_enforce_privileged.sh
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e_chart/test_pss_enforce_privileged.sh \
        .github/workflows/chart-e2e.yml
git commit -m "feat(tests/e2e_chart): PSS enforce=restricted rejection e2e shell test (D2.5 part 3)"
```

---

### Task 18: MSW setup — handlers, server, vitest integration (D2.6 — part 1 of 4)

**Files:**

- Create: `frontend/tests/mocks/handlers.ts`
- Create: `frontend/tests/mocks/server.ts`
- Create: `frontend/tests/mocks/setup.ts`
- Modify: `frontend/package.json` (add `msw` to devDependencies)
- Modify: `frontend/vitest.config.ts`

- [ ] **Step 1: Add the dep**

```bash
cd frontend
pnpm add -D msw@^2.4
```

Expected: `msw` resolves to ^2.4.x in `pnpm-lock.yaml`.

- [ ] **Step 2: Create `mocks/handlers.ts`**

```typescript
import { http, HttpResponse } from "msw";

/**
 * Centrally-registered MSW handlers (D2.6).
 *
 * Tests may locally override a handler via `server.use(http.post(...))`.
 * E2E does NOT use MSW — it runs against the real backend in k3d.
 */
export const handlers = [
  http.get("/api/v1/users/me", () =>
    HttpResponse.json({
      id: "00000000-0000-0000-0000-000000000001",
      email: "msw@example.com",
      role: "developer",
    }),
  ),

  http.get("/api/v1/jobs", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-0000000000aa",
          type: "train",
          status: "queued_backend",
          detector_id: "00000000-0000-0000-0000-000000000022",
          submitted_at: "2026-05-16T10:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    }),
  ),

  http.get("/api/v1/detectors", () =>
    HttpResponse.json({
      items: [
        {
          id: "00000000-0000-0000-0000-000000000022",
          name: "elfrfdet",
          owner_email: "msw@example.com",
          visibility: "private",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    }),
  ),

  http.post("/api/v1/jobs", () =>
    HttpResponse.json(
      {
        id: "00000000-0000-0000-0000-0000000000bb",
        type: "train",
        status: "queued_backend",
      },
      { status: 201 },
    ),
  ),
];
```

- [ ] **Step 3: Create `mocks/server.ts`**

```typescript
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export const server = setupServer(...handlers);
```

- [ ] **Step 4: Create `mocks/setup.ts`**

```typescript
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

`onUnhandledRequest: "error"` enforces anti-flaky rule #1 (no un-mocked network).

- [ ] **Step 5: Wire into `vitest.config.ts`**

Replace the config with:

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts", "./tests/mocks/setup.ts"],
    include: [
      "tests/unit/**/*.test.{ts,tsx}",
      "tests/integration/**/*.test.{ts,tsx}",
      "tests/contract/**/*.test.{ts,tsx}",
    ],
    testTimeout: 10_000,
    coverage: {
      reporter: ["text", "html", "lcov"],
      include: [
        "src/lib/**",
        "src/hooks/**",
        "src/components/**", // D2.10 — extended
        "src/routes/**", // D2.10 — extended
      ],
      thresholds: {
        lines: 70,
        functions: 70,
        statements: 70,
      },
    },
  },
});
```

- [ ] **Step 6: Verify the unit tier still passes**

```bash
cd frontend
pnpm test
```

Expected: existing unit tests still green; MSW global listen prints `onUnhandledRequest: "error"` and no unit test trips it (because unit tests never call `fetch`).

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml \
        frontend/tests/mocks/ frontend/vitest.config.ts
git commit -m "feat(frontend/tests): add MSW handlers + server + vitest integration setup (D2.6 part 1)"
```

---

### Task 19: Frontend integration test — `routes/jobs` (D2.6 — part 2 of 4)

**Files:**

- Create: `frontend/tests/integration/routes/jobs.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
/**
 * D2.6 — Integration test: /jobs route renders the MSW-mocked list.
 *
 * This catches:
 * - schema_gen drift (handler payload shape vs route expectation)
 * - lost query-key wiring between TanStack Query and the route loader
 */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { routes } from "@/routes";

function renderJobsRoute() {
  const router = createMemoryRouter(routes, { initialEntries: ["/jobs"] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("/jobs route", () => {
  it("renders the MSW-mocked job row", async () => {
    renderJobsRoute();
    await waitFor(() =>
      expect(screen.getByText(/queued_backend/i)).toBeInTheDocument(),
    );
  });

  it("shows empty state when MSW returns zero items", async () => {
    const { server } = await import("@/../tests/mocks/server");
    const { http, HttpResponse } = await import("msw");
    server.use(
      http.get("/api/v1/jobs", () =>
        HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 }),
      ),
    );

    renderJobsRoute();
    await waitFor(() =>
      expect(screen.getByText(/no jobs/i)).toBeInTheDocument(),
    );
  });
});
```

Adjust `routes` import path to match the actual route registry (e.g. `@/router` or `@/main.tsx` exports).

- [ ] **Step 2: Run**

```bash
cd frontend
pnpm test tests/integration/routes/jobs.test.tsx
```

Expected: 2 passed. If the empty-state text is different (the route may render `"No jobs found"` or `"暫無資料"` for zh-TW), update the assertion to match the actual literal.

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/integration/routes/jobs.test.tsx
git commit -m "feat(frontend/tests): integration test for /jobs route with MSW (D2.6 part 2)"
```

---

### Task 20: Frontend integration test — `routes/detectors` (D2.6 — part 3 of 4)

**Files:**

- Create: `frontend/tests/integration/routes/detectors.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
/**
 * D2.6 — Integration test: /detectors route renders MSW-mocked entries.
 */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { routes } from "@/routes";

function renderDetectorsRoute() {
  const router = createMemoryRouter(routes, { initialEntries: ["/detectors"] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("/detectors route", () => {
  it("renders the MSW-mocked detector row", async () => {
    renderDetectorsRoute();
    await waitFor(() =>
      expect(screen.getByText(/elfrfdet/i)).toBeInTheDocument(),
    );
  });

  it("shows the visibility badge", async () => {
    renderDetectorsRoute();
    await waitFor(() =>
      expect(screen.getByText(/private/i)).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 2: Run**

```bash
cd frontend
pnpm test tests/integration/routes/detectors.test.tsx
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/integration/routes/detectors.test.tsx
git commit -m "feat(frontend/tests): integration test for /detectors route with MSW (D2.6 part 3)"
```

---

### Task 21: Frontend integration test — `JobSubmitForm` full flow (D2.6 — part 4 of 4)

**Files:**

- Create: `frontend/tests/integration/forms/JobSubmitForm.flow.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
/**
 * D2.6 — Integration test: JobSubmitForm fills + submits + observes the
 * MSW-mocked 201 response.
 */
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { JobSubmitForm } from "@/components/forms/JobSubmitForm";

function renderWithProviders(ui: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("JobSubmitForm full flow", () => {
  it("submits and surfaces success state", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobSubmitForm jobType="train" />);

    // Wait for detector list (from MSW) to populate.
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: /detector/i }),
      ).toBeInTheDocument(),
    );

    // Pick the detector + version + dataset combobox values.
    await user.click(screen.getByRole("combobox", { name: /detector/i }));
    await user.click(screen.getByRole("option", { name: /elfrfdet/i }));

    // Submit.
    await user.click(screen.getByRole("button", { name: /submit/i }));

    // The handler returns 201; the form should surface a "submitted" toast / banner.
    await waitFor(() =>
      expect(screen.getByText(/submitted|queued|success/i)).toBeInTheDocument(),
    );
  });

  it("surfaces a 422 error when MSW returns ValidationError", async () => {
    const user = userEvent.setup();
    const { server } = await import("@/../tests/mocks/server");
    const { http, HttpResponse } = await import("msw");

    server.use(
      http.post("/api/v1/jobs", () =>
        HttpResponse.json(
          {
            detail: {
              code: "user_params_invalid",
              message: "epochs must be > 0",
            },
          },
          { status: 422 },
        ),
      ),
    );

    renderWithProviders(<JobSubmitForm jobType="train" />);
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: /detector/i }),
      ).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() =>
      expect(screen.getByText(/epochs must be > 0/i)).toBeInTheDocument(),
    );
  });
});
```

If `JobSubmitForm`'s actual prop API or copy differs, adjust the assertions to match. The structural intent (render → fill → submit → observe 201 / 422) must stay.

- [ ] **Step 2: Run**

```bash
cd frontend
pnpm test tests/integration/forms/JobSubmitForm.flow.test.tsx
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/integration/forms/JobSubmitForm.flow.test.tsx
git commit -m "feat(frontend/tests): JobSubmitForm full-flow integration test with MSW (D2.6 part 4)"
```

---

### Task 22: Visual snapshot — `rjsf_form_snapshots.spec.ts` (D2.7 — part 1 of 3)

**Files:**

- Create: `frontend/tests/visual/rjsf_form_snapshots.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
/**
 * D2.7 — Visual regression: RJSF v6 form rendering.
 *
 * Snapshots the JobSubmitForm in three states: empty, partially filled,
 * showing a validation error. Catches CSS workaround regressions
 * (architecture.md §10 #19) and RJSF template drift on dependency bumps.
 *
 * Run: pnpm playwright test --grep "RJSF visual"
 * Update baselines: pnpm playwright test --update-snapshots --grep "RJSF visual"
 */
import { test, expect } from "@playwright/test";
import { loginAs } from "../e2e/helpers";

test.describe("RJSF visual", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/jobs/new?type=train");
    await page.waitForLoadState("networkidle");
  });

  test("empty train form", async ({ page }) => {
    const form = page.locator('form[data-testid="job-submit-form"]');
    await expect(form).toHaveScreenshot("rjsf-train-empty.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("partially filled train form", async ({ page }) => {
    await page.getByRole("combobox", { name: /detector/i }).click();
    await page
      .getByRole("option", { name: /elfrfdet/i })
      .first()
      .click();
    await page.waitForLoadState("networkidle");
    const form = page.locator('form[data-testid="job-submit-form"]');
    await expect(form).toHaveScreenshot("rjsf-train-partial.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("validation error displayed", async ({ page }) => {
    // Force an empty submit to surface field-level error UI.
    await page.getByRole("button", { name: /submit/i }).click();
    await page.waitForSelector('[role="alert"], .text-destructive', {
      timeout: 2_000,
    });
    const form = page.locator('form[data-testid="job-submit-form"]');
    await expect(form).toHaveScreenshot("rjsf-train-error.png", {
      maxDiffPixelRatio: 0.02,
    });
  });
});
```

This requires `JobSubmitForm` to render a wrapping `<form data-testid="job-submit-form">`. If absent, add the `data-testid` attribute first (one-line code change in `frontend/src/components/forms/JobSubmitForm.tsx`).

- [ ] **Step 2: Generate baseline screenshots**

```bash
cd frontend
pnpm playwright test --update-snapshots --grep "RJSF visual"
```

Expected: three new PNGs land under `tests/visual/__screenshots__/`.

- [ ] **Step 3: Re-run to verify pixel-stable**

```bash
cd frontend
pnpm playwright test --grep "RJSF visual"
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/forms/JobSubmitForm.tsx \
        frontend/tests/visual/rjsf_form_snapshots.spec.ts \
        frontend/tests/visual/__screenshots__/
git commit -m "feat(frontend/tests): visual snapshot for RJSF form rendering (D2.7 part 1)"
```

---

### Task 23: Visual snapshot — `sidebar_snapshots.spec.ts` (D2.7 — part 2 of 3)

**Files:**

- Create: `frontend/tests/visual/sidebar_snapshots.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
/**
 * D2.7 — Visual regression: Sidebar collapsed / expanded / mobile drawer.
 */
import { test, expect } from "@playwright/test";
import { loginAs } from "../e2e/helpers";

test.describe("Sidebar visual", () => {
  test("expanded sidebar (desktop)", async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/jobs");
    await page.waitForLoadState("networkidle");
    const sidebar = page.locator('[data-sidebar="sidebar"]').first();
    await expect(sidebar).toHaveScreenshot("sidebar-expanded.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("collapsed sidebar (desktop)", async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/jobs");
    await page.waitForLoadState("networkidle");
    // Click the collapse toggle (typically the button with aria-label "Toggle sidebar")
    await page.getByRole("button", { name: /toggle sidebar/i }).click();
    await page.waitForTimeout(300); // wait for animation; transient — allowed by anti-flaky rule #5
    const sidebar = page.locator('[data-sidebar="sidebar"]').first();
    await expect(sidebar).toHaveScreenshot("sidebar-collapsed.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("mobile drawer (iphone-13-mini)", async ({ page, browser }) => {
    const context = await browser.newContext({
      ...require("@playwright/test").devices["iPhone 13 Mini"],
    });
    const mobilePage = await context.newPage();
    await loginAs(mobilePage, "developer");
    await mobilePage.goto("/jobs");
    await mobilePage.waitForLoadState("networkidle");
    await mobilePage.getByRole("button", { name: /menu/i }).click();
    await mobilePage.waitForTimeout(300); // drawer animation
    await expect(mobilePage).toHaveScreenshot("sidebar-mobile-drawer.png", {
      fullPage: false,
      maxDiffPixelRatio: 0.02,
    });
    await context.close();
  });
});
```

- [ ] **Step 2: Generate baselines + verify**

```bash
cd frontend
pnpm playwright test --update-snapshots --grep "Sidebar visual"
pnpm playwright test --grep "Sidebar visual"
```

Expected: 3 baselines created; second run is 3 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/tests/visual/sidebar_snapshots.spec.ts \
        frontend/tests/visual/__screenshots__/
git commit -m "feat(frontend/tests): visual snapshot for Sidebar (expanded/collapsed/mobile) (D2.7 part 2)"
```

---

### Task 24: Visual snapshot — `page_header_snapshots.spec.ts` (D2.7 — part 3 of 3)

**Files:**

- Create: `frontend/tests/visual/page_header_snapshots.spec.ts`

- [ ] **Step 1: Write the spec**

```typescript
/**
 * D2.7 — Visual regression: PageHeader / breadcrumbs / role badge.
 */
import { test, expect } from "@playwright/test";
import { loginAs } from "../e2e/helpers";

test.describe("PageHeader visual", () => {
  test("/jobs header as developer persona", async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/jobs");
    await page.waitForLoadState("networkidle");
    const header = page.locator('[data-testid="page-header"]');
    await expect(header).toHaveScreenshot("header-jobs-developer.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("/jobs header as admin persona (renders admin badge)", async ({
    page,
  }) => {
    await loginAs(page, "admin");
    await page.goto("/jobs");
    await page.waitForLoadState("networkidle");
    const header = page.locator('[data-testid="page-header"]');
    await expect(header).toHaveScreenshot("header-jobs-admin.png", {
      maxDiffPixelRatio: 0.01,
    });
  });

  test("/detectors/{id} breadcrumb expansion", async ({ page }) => {
    await loginAs(page, "developer");
    await page.goto("/detectors/00000000-0000-0000-0000-000000000022");
    await page.waitForLoadState("networkidle");
    const header = page.locator('[data-testid="page-header"]');
    await expect(header).toHaveScreenshot("header-detector-detail.png", {
      maxDiffPixelRatio: 0.01,
    });
  });
});
```

The header component should carry `data-testid="page-header"`. If it doesn't, add the attribute as a one-liner in `frontend/src/components/layout/PageHeader.tsx`.

- [ ] **Step 2: Generate baselines + verify**

```bash
cd frontend
pnpm playwright test --update-snapshots --grep "PageHeader visual"
pnpm playwright test --grep "PageHeader visual"
```

Expected: 3 baselines; second run is 3 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/layout/PageHeader.tsx \
        frontend/tests/visual/page_header_snapshots.spec.ts \
        frontend/tests/visual/__screenshots__/
git commit -m "feat(frontend/tests): visual snapshot for PageHeader / role badge / breadcrumb (D2.7 part 3)"
```

---

### Task 25: Frontend contract — `schema_gen_drift.test.ts` (D2.8)

**Files:**

- Create: `frontend/tests/contract/schema_gen_drift.test.ts`

- [ ] **Step 1: Write the test**

`schema.gen.ts` contains two hand-stitched fields (architecture.md §10 #14): `detector_defaults` on `JobRead` and `"gpu1"` in `ResourceProfile`. The test asserts both still exist in the live backend's `/openapi.json`. If they do, the hand-stitched extension can be retired; if they don't, the test stays green — the contract is enforced (hand-stitched fields remain valid).

```typescript
/**
 * D2.8 — Contract test: schema.gen.ts hand-stitched fields stay
 * consistent with the live backend's /openapi.json.
 *
 * Loads /openapi.json from the running backend (via the same MSW handler
 * that serves it in dev, or via a real-backend env if E2E_BACKEND_URL is
 * set). Asserts the two hand-stitched extensions:
 *   - JobRead.detector_defaults exists
 *   - ResourceProfile enum includes "gpu1"
 *
 * When backend ships both natively, this test goes green and the
 * hand-stitched schema.handstitched.ts can be deleted (Phase 3 R5).
 */
import { describe, expect, it } from "vitest";

const OPENAPI_URL =
  process.env.OPENAPI_URL ?? "http://localhost:8000/openapi.json";

describe("schema.gen.ts contract drift", () => {
  let openapi: any;

  it.beforeAll(async () => {
    const resp = await fetch(OPENAPI_URL);
    expect(resp.status).toBe(200);
    openapi = await resp.json();
  });

  it("JobRead.detector_defaults is present in /openapi.json", () => {
    const jobRead = openapi.components.schemas.JobRead;
    expect(jobRead).toBeDefined();
    expect(jobRead.properties).toHaveProperty("detector_defaults");
  });

  it("ResourceProfile enum contains 'gpu1'", () => {
    const profile = openapi.components.schemas.ResourceProfile;
    expect(profile.enum).toContain("gpu1");
  });
});
```

This test needs a running backend. Two options for CI:

- (a) Run vitest in `backend-fast.yml` after `uvicorn` is up — adds backend boot to a frontend test, dirty.
- (b) Snapshot `/openapi.json` into the repo (`frontend/tests/fixtures/openapi.snapshot.json`) and diff against it; the snapshot updates whenever `pnpm gen-api-types` runs.

**Mainstream practice (chosen here): option (b).** Modify the test to read from the snapshot file; the snapshot is committed alongside `schema.gen.ts`.

Replace `fetch(OPENAPI_URL)` with:

```typescript
import openapiSnapshot from "../fixtures/openapi.snapshot.json";

describe("schema.gen.ts contract drift", () => {
  it("JobRead.detector_defaults is present in committed /openapi.json snapshot", () => {
    expect(
      openapiSnapshot.components.schemas.JobRead.properties,
    ).toHaveProperty("detector_defaults");
  });

  it("ResourceProfile enum contains 'gpu1'", () => {
    expect(openapiSnapshot.components.schemas.ResourceProfile.enum).toContain(
      "gpu1",
    );
  });
});
```

Generate the snapshot via:

```bash
cd backend
uv run python -c "
import json
from app.main import app
print(json.dumps(app.openapi(), indent=2))
" > ../frontend/tests/fixtures/openapi.snapshot.json
```

Wire snapshot regen into `frontend/package.json` scripts:

```json
"gen-api-types": "openapi-typescript ../backend && ./scripts/regen-openapi-snapshot.sh"
```

Create `frontend/scripts/regen-openapi-snapshot.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
uv --project backend run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" \
  > frontend/tests/fixtures/openapi.snapshot.json
```

```bash
chmod +x frontend/scripts/regen-openapi-snapshot.sh
```

- [ ] **Step 2: Generate the snapshot**

```bash
cd backend
mkdir -p ../frontend/tests/fixtures
uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))" \
  > ../frontend/tests/fixtures/openapi.snapshot.json
```

- [ ] **Step 3: Run the test**

```bash
cd frontend
pnpm test tests/contract/schema_gen_drift.test.ts
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/contract/schema_gen_drift.test.ts \
        frontend/tests/fixtures/openapi.snapshot.json \
        frontend/scripts/regen-openapi-snapshot.sh \
        frontend/package.json
git commit -m "feat(frontend/tests): schema_gen_drift contract test + openapi snapshot (D2.8)"
```

---

### Task 26: Reactivate `frontend-slow.yml` (D2.9)

**Files:**

- Create: `.github/workflows/frontend-slow.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: frontend-slow

# D2.9 — Playwright E2E + visual snapshots against a k3d cluster running
# the chart. Informational only (not a required check) — slow-tier failures
# get fixed forward, not reverted.

on:
  push:
    branches: [main]
  schedule:
    - cron: "0 4 * * *" # daily 04:00 UTC

permissions:
  contents: read

concurrency:
  group: frontend-slow-${{ github.ref }}
  cancel-in-progress: false # never cancel main / nightly

jobs:
  playwright:
    name: playwright
    runs-on: ubuntu-24.04
    timeout-minutes: 35
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

      - name: Setup pnpm + node
        uses: ./.github/actions/setup-pnpm-node

      - name: Install playwright browsers
        run: pnpm --dir frontend exec playwright install --with-deps chromium

      - name: Install k3d
        run: |
          curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
          k3d version

      - name: Create k3d cluster (no bundled Traefik)
        run: |
          k3d cluster create lolday-fe-slow \
            --servers 1 --agents 0 \
            --k3s-arg "--disable=traefik@server:*" \
            --k3s-arg "--disable=servicelb@server:*" \
            --wait

      - name: Install chart
        run: |
          helm install lolday charts/lolday -f charts/lolday/values-test.yaml --wait --timeout 5m
          kubectl rollout status -n lolday deploy/lolday-backend --timeout=300s
          kubectl rollout status -n lolday deploy/lolday-frontend --timeout=300s

      - name: Port-forward backend + frontend
        run: |
          kubectl -n lolday port-forward svc/lolday-frontend 5173:80 &
          kubectl -n lolday port-forward svc/lolday-backend 8000:80 &
          sleep 5
          curl -sf http://localhost:8000/healthz
          curl -sf http://localhost:5173/

      - name: Run playwright E2E + visual
        env:
          E2E_BASE_URL: http://localhost:5173
        run: pnpm --dir frontend playwright test --reporter=github

      - name: Upload trace + screenshots on failure
        if: failure()
        uses: actions/upload-artifact@b4b15b8c7c6ac21ea08fcf65892d2ee8f75cf882 # v4.4.3
        with:
          name: playwright-trace
          path: |
            frontend/test-results/
            frontend/tests/visual/__screenshots__/

      - name: Teardown
        if: always()
        run: k3d cluster delete lolday-fe-slow || true
```

- [ ] **Step 2: Verify the workflow YAML is well-formed**

```bash
yamllint .github/workflows/frontend-slow.yml
gh workflow view frontend-slow --yaml || true   # confirms GH parses it (will say no runs yet)
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/frontend-slow.yml
git commit -m "feat(ci): reactivate frontend-slow.yml — playwright E2E + visual against k3d (D2.9)"
```

The workflow will first run on the next `main` push (squash-merge of this branch). Promotion to required-check is **not** part of Phase 2.

---

### Task 27: Extend vitest coverage to `src/components/` + `src/routes/` (D2.10 — part 1 of 2)

**Files:**

- Modify: `frontend/vitest.config.ts` (already done in Task 18 — verify)
- Add: `frontend/tests/unit/components/` (smoke tests if any component currently has zero coverage)

- [ ] **Step 1: Verify the `vitest.config.ts` change from Task 18**

```bash
cd frontend
grep -A6 "coverage:" vitest.config.ts
```

Expected: `include` lists `src/components/**` and `src/routes/**`; `thresholds.lines = 70`.

- [ ] **Step 2: Measure current coverage**

```bash
cd frontend
pnpm test --coverage
```

Read the summary table. Note any file in `src/components/` or `src/routes/` < 70 % — that's a gap to fill.

- [ ] **Step 3: Backfill component smoke tests**

For each file with < 70 % coverage, add a smoke test at `frontend/tests/unit/components/<area>/<Component>.smoke.test.tsx` along the lines of:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";

describe("JobStatusBadge smoke", () => {
  it("renders the queued_backend label", () => {
    render(<JobStatusBadge status="queued_backend" />);
    expect(screen.getByText(/queued/i)).toBeInTheDocument();
  });

  it("renders the failed label", () => {
    render(<JobStatusBadge status="failed" />);
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});
```

Use polyfactory-style fixture data sparingly — the goal is to bring the line coverage above 70, not to write integration-style tests at the unit tier.

- [ ] **Step 4: Re-measure coverage**

```bash
cd frontend
pnpm test --coverage
```

Expected: `% Lines` row for `src/components/**` and `src/routes/**` ≥ 70.

- [ ] **Step 5: Commit**

```bash
git add frontend/tests/unit/components/ frontend/vitest.config.ts
git commit -m "feat(frontend/tests): extend vitest coverage to src/components/ + src/routes/ at 70% (D2.10 part 1)"
```

---

### Task 28: Raise Codecov gate to frontend 70 % (D2.10 — part 2 of 2)

**Files:**

- Modify: `.codecov.yml`

- [ ] **Step 1: Update the project gate**

Open `.codecov.yml` and add (or update) the frontend flag:

```yaml
coverage:
  status:
    project:
      default:
        target: 80%
        threshold: 1%
        flags:
          - backend
      frontend:
        target: 70%
        threshold: 1%
        flags:
          - frontend
    patch:
      default:
        target: 80%
      frontend:
        target: 70%

flags:
  backend:
    paths:
      - backend/
  frontend:
    paths:
      - frontend/src/

comment:
  layout: "diff, flags, files"
```

- [ ] **Step 2: Wire codecov upload in `frontend.yml`**

Open `.github/workflows/frontend.yml`, locate the `pnpm test` step, and add a follow-up:

```yaml
- name: Upload coverage to Codecov
  uses: codecov/codecov-action@e28ff129e5465c2c0dcc6f003fc735cb6ae0c673 # v4.5.0
  with:
    files: ./frontend/coverage/lcov.info
    flags: frontend
    fail_ci_if_error: false # informational until D2.10 stabilises
```

(Pin via `gh api`: `gh api /repos/codecov/codecov-action/git/refs/tags/v4.5.0 --jq .object.sha`.)

- [ ] **Step 3: Commit**

```bash
git add .codecov.yml .github/workflows/frontend.yml
git commit -m "feat(ci): raise Codecov frontend gate to 70% over src/components + src/routes (D2.10 part 2)"
```

---

### Task 29: Phase 2 exit verification

**Files:** (verification only — no new files)

- [ ] **Step 1: Confirm every D2.x deliverable has at least one committed file**

Run from repo root:

```bash
git log --since="2026-05-16" --pretty=format: --name-only | sort -u | \
  grep -E '^(backend/app/services/(job_validation|job_submission|job_dispatch)\.py|backend/app/auth/cf_access\.py|backend/tests/contract/openapi/test_mlflow_authz_cross_user\.py|backend/tests/heavy/(mlflow/test_acl_real_multi_user|postgres/test_audit_log_durability|auth/test_jwks_reflector)\.py|backend/tests/integration/(routers/(test_csrf_token_rotation|test_rate_limit_user_vs_ip)|services/(test_auth_dev_personas|test_jwks_cache_ttl))\.py|tests/e2e_chart/test_(kyverno_unsigned_image_rejected|pss_enforce_privileged)\.sh|frontend/tests/(mocks|integration|visual|contract|fixtures)/.*|frontend/vitest\.config\.ts|.github/workflows/(chart-e2e|frontend-slow)\.yml|.codecov\.yml)' | sort
```

Expected: every Phase 2 D2.x file appears.

- [ ] **Step 2: Run the full fast tier**

```bash
cd backend && uv run pytest -q -m "not heavy"
cd ../frontend && pnpm test --coverage
```

Expected (backend): fast tier passes; expect ≥ 843 + the new Phase 2 fast-tier tests.

Expected (frontend): unit + integration + contract green; coverage row for `src/components/` + `src/routes/` ≥ 70 %.

- [ ] **Step 3: Run the heavy tier**

```bash
cd backend
uv run pytest -m heavy --timeout=180
```

Expected: every Phase 2 heavy test passes (D2.3 part 2, part 5; D2.4 part 1). Total heavy count = previous (5) + 3 new = 8.

- [ ] **Step 4: Run the contract tier explicitly**

```bash
cd backend
uv run pytest tests/contract/ -m contract --timeout=180
```

Expected: every Phase 1 + Phase 2 contract test passes; no xfail left on `test_schemathesis_users_me.py::422`.

- [ ] **Step 5: Verify `chart-e2e.yml` ran green at least once on main**

```bash
gh run list -w chart-e2e -L 3
```

Expected: most recent run on `main` is success and includes both new D2.5 shell steps.

- [ ] **Step 6: Verify `frontend-slow.yml` ran green at least once on main**

```bash
gh run list -w frontend-slow -L 3
```

Expected: workflow exists; first run on main after merge is success.

- [ ] **Step 7: Verify branch protection unchanged**

```bash
gh api repos/bolin8017/lolday/branches/main/protection/required_status_checks \
  --jq '.contexts | length'
```

Expected: `9` (same as Phase 1). Phase 2 deliberately leaves the slow tier informational; no Phase 2 task promotes a new required check. Promotion is a separate operator decision after Phase 2 settles.

- [ ] **Step 8: Update `docs/architecture.md` §10 with closure notes**

Append entries 28 / 29 / 30 (or whichever the next numbers are) to `docs/architecture.md` §10, marking:

- Item 28: `~~GET /api/v1/users/me missing 422 OpenAPI response~~ — resolved Phase 2 D2.3 Task 8.`
- Item 29: `~~chart-e2e.yml red on k3d bundled Traefik vs traefik.io/v1alpha1~~ — resolved Phase 2 D2.5 Task 15 (k3d cluster create --k3s-arg "--disable=traefik@server:*"). Chart Traefik settings unchanged.`
- Item 30 (new tech-debt surfaced by Phase 2, if any): noted with **Owner:** and follow-up plan.

- [ ] **Step 9: Author the Phase 2 ship auto-memory**

Save to `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/project_test_architecture_phase_2_shipped.md` (template — adapt as needed):

```markdown
---
name: test-architecture-phase-2-shipped
description: "Test architecture redesign Phase 2 shipped <date>; service-extracted routers/jobs, multi-persona dev auth, four security regression gates, JWKS contracts, k3d Kyverno+PSS e2e, frontend MSW + visual + contract tiers, frontend-slow.yml reactivated."
metadata:
  type: project
---

Phase 2 shipped <date> as PR #<N>. Spec §10 D2.1 – D2.10.

**Why:** Phase 1 scaffolded the layered tiers; Phase 2 fills the security
boundary + frontend gaps + closes the §10.28-30 follow-ups.

**How to apply:** future security-relevant backend / frontend work follows
the new contract / heavy patterns. Service-extracted job submission means
hypothesis + schemathesis can target pure functions instead of TestClient.
Multi-persona dev auth unblocks Phase 3 multi-persona Playwright parallel.

What landed:

- R3 service extraction: routers/jobs.py shrunk from 916 → <new> lines;
  job_validation / job_submission / job_dispatch are pure-function services.
- R4 AUTH_DEV_PERSONAS: X-Dev-Persona header switches dev-mode persona per request.
- D2.3 four security gates added (contract + heavy combinations).
- D2.4 JWKS reflector + cache TTL boundary.
- D2.5 chart-e2e turned green (k3d bundled Traefik dropped); Kyverno + PSS shell tests.
- D2.6 frontend MSW + 3 integration suites.
- D2.7 frontend visual snapshots (RJSF + sidebar + page header).
- D2.8 frontend openapi snapshot contract.
- D2.9 frontend-slow.yml reactivated against k3d.
- D2.10 vitest coverage at 70% over src/components + src/routes; Codecov frontend gate raised.

Real bugs / inconsistencies caught: <fill in if any>.

Tech debt closed: architecture.md §10 #28 (/users/me 422), §10 #29 (chart-e2e Traefik).
Tech debt surfaced: <fill in if any>.

Phase 3 unblocked: multi-persona Playwright parallel (R4 done).
```

- [ ] **Step 10: Commit + create PR**

```bash
git add docs/architecture.md
git commit -m "docs(architecture): close §10 #28 (users_me 422) + #29 (chart-e2e Traefik) post-Phase-2"

git push -u origin <branch>
gh pr create --title "feat(test-architecture): Phase 2 — Security boundaries, frontend integration, R3 + R4" \
  --body "$(cat <<'EOF'
## Summary
- D2.1 R3 — routers/jobs.py service extraction (job_validation / job_submission / job_dispatch)
- D2.2 R4 — AUTH_DEV_PERSONAS multi-persona dev mode (unblocks Phase 3 Playwright parallel)
- D2.3 security gates — cross-user MLflow ACL contract + heavy real-MLflow, CSRF flow, rate-limit user-vs-IP, audit-log durability
- D2.4 auth contracts — JWKS reflector + cache TTL
- D2.5 k3d Kyverno + PSS enforce e2e (also closes architecture.md §10.29 — k3d bundled Traefik conflict)
- D2.6 frontend MSW + 3 integration suites
- D2.7 frontend visual snapshots (RJSF / Sidebar / PageHeader)
- D2.8 frontend OpenAPI snapshot contract (defers Phase 3 R5 by guarding the two handstitched fields)
- D2.9 frontend-slow.yml reactivated against k3d
- D2.10 vitest coverage extended to src/components + src/routes at 70%; Codecov frontend gate raised
- Closes architecture.md §10 #28 (GET /users/me missing 422)

## Test plan
- [ ] backend-fast green
- [ ] backend-slow green (heavy tier)
- [ ] chart-e2e green (Kyverno + PSS shell tests)
- [ ] frontend-fast green; coverage ≥ 70% on src/components + src/routes
- [ ] frontend-slow green on main after merge
- [ ] Phase 2 exit verification (Task 29 steps 1-7)

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 Phase 2.
Plan: docs/superpowers/plans/2026-05-16-test-architecture-phase-2.md.
EOF
)"
```

---

## Summary

Phase 2 lands the spec §10 Phase 2 deliverables on top of the Phase-1 scaffolding:

- **Backend service extraction** (D2.1 / R3) — three pure services (`job_validation` / `job_submission` / `job_dispatch`) carve the testable business logic out of `routers/jobs.py`. The router stays as a thin HTTP adapter.
- **Multi-persona dev auth** (D2.2 / R4) — `X-Dev-Persona` header overrides the single `AUTH_DEV_EMAIL` for the request. This is the unblock for Phase 3's multi-persona Playwright parallel.
- **Security regression gates** (D2.3) — cross-user MLflow ACL (contract + heavy on real MLflow), CSRF origin flow, rate-limit per-user-vs-IP boundary, audit-log durability on real PG.
- **Auth contracts** (D2.4) — JWKS reflector heavy tier covers the real verification path; freezegun pins the cache TTL boundary.
- **Chart e2e turns green** (D2.5) — k3d's bundled Traefik dropped (closes §10.29); new shell tests run Kyverno unsigned-image rejection + PSS privileged rejection under Enforce / restricted patches.
- **Frontend integration tier** (D2.6) — MSW handlers + three vitest integration tests (`/jobs`, `/detectors`, `JobSubmitForm` full flow).
- **Frontend visual tier** (D2.7) — playwright screenshot snapshots for RJSF form, Sidebar (3 states), PageHeader (3 personas/routes).
- **Frontend OpenAPI contract** (D2.8) — committed snapshot guards the two hand-stitched fields until Phase 3 R5.
- **Frontend slow tier reactivated** (D2.9) — `frontend-slow.yml` runs playwright against k3d on `main` + nightly.
- **Coverage gate raised** (D2.10) — vitest threshold 70 % over `src/components/` + `src/routes/`; Codecov frontend project gate matches.

Total: **29 tasks**, roughly grouped into ten D2.x deliverable phases plus the exit verification.

## Test plan

End-to-end verification — done as Task 29:

- [ ] Full backend fast tier green (`pytest -m "not heavy"`).
- [ ] Full backend heavy tier green (`pytest -m heavy --timeout=180`).
- [ ] Contract tier green; `test_schemathesis_users_me.py` no longer xfails on 422.
- [ ] `chart-e2e.yml` runs the two D2.5 shell steps green on main.
- [ ] `frontend-slow.yml` runs green on main after merge.
- [ ] Frontend vitest coverage ≥ 70 % on `src/components/**` + `src/routes/**`.
- [ ] Codecov reports the frontend project gate at 70 %; PR comments include the flag.
- [ ] Phase 2 exit summary auto-memory committed.
- [ ] `docs/architecture.md` §10 #28 + #29 marked resolved.

## Self-Review Coverage

| Spec §10 deliverable            | Plan task(s)  | Notes                                                                                                             |
| ------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------- |
| D2.1 R3                         | Tasks 1 – 5   | `job_validation` / `job_submission` / `job_dispatch` extracted; router slimmed                                    |
| D2.2 R4                         | Tasks 6 – 7   | `AUTH_DEV_PERSONAS` config + `X-Dev-Persona` header in backend; `loginAs(page, role)` helper in frontend          |
| D2.3 security gates             | Tasks 8 – 12  | Cross-user MLflow contract + heavy, CSRF, rate-limit, audit-log durability; also closes `/users/me` 422 in Task 8 |
| D2.4 auth contracts             | Tasks 13 – 14 | JWKS reflector heavy + JWKS cache TTL with freezegun                                                              |
| D2.5 Kyverno + PSS enforce e2e  | Tasks 15 – 17 | Task 15 unblocks §10.29 first; Tasks 16-17 add the shell tests                                                    |
| D2.6 frontend MSW + integration | Tasks 18 – 21 | mocks, two `/routes/*` suites, one `JobSubmitForm` full-flow                                                      |
| D2.7 frontend visual            | Tasks 22 – 24 | RJSF, Sidebar, PageHeader                                                                                         |
| D2.8 frontend contract          | Task 25       | OpenAPI snapshot covers handstitched fields                                                                       |
| D2.9 frontend-slow.yml          | Task 26       | New workflow, informational only                                                                                  |
| D2.10 vitest coverage + Codecov | Tasks 27 – 28 | Threshold 70%; Codecov gate raised                                                                                |
| Exit verification               | Task 29       | Full-suite + archive + auto-memory + PR                                                                           |

| Phase 1 lesson                                              | Where baked in                                                                                                         |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Bite-size H/T subtask granularity                           | Tasks 1 – 29 follow Phase 1's `Task N` shape; each step is 2 – 5 min                                                   |
| Contract-tier `@pytest.mark.timeout(180)`                   | Task 8 (mlflow_authz cross-user) inherits via `pytestmark = [pytest.mark.contract, pytest.mark.timeout(180)]`          |
| Use in-house `mock_k8s_batch`, not `kubernetes-fake-client` | Task 3 unit test imports the autouse fixture from Phase 1 conftest                                                     |
| `_reenable_app_loggers` autouse                             | Tasks 6 / 14 inherit via `integration/services/conftest.py`; heavy/auth conftest copies pattern                        |
| Helm-unittest fixtures lock chart defaults                  | D2.5 (Tasks 16 – 17) leaves the chart at Audit / baseline and patches at runtime; helm-unittest fixtures unchanged     |
| Branch protection skip-companion                            | None added in Phase 2 — every new test gate stays informational on slow tier; required-check promotion is post-Phase-2 |
| k3d bundled Traefik blocker                                 | Task 15 lands the `--k3s-arg "--disable=traefik@server:*"` fix **before** Tasks 16 – 17 layer e2e shell tests on top   |

## Out-of-scope (handled by separate plans or PRs)

- **R5 schema.gen.ts split** — Phase 3 deliverable D3.8 owns the three-file refactor (`schema.gen.ts` / `schema.handstitched.ts` / `schema.ts`). Phase 2 D2.8 only guards the two handstitched fields against drift; no code restructure.
- **Multi-persona Playwright parallel** — Phase 3 D3.4 enables `fullyParallel: true` once R4 has soaked. Phase 2 ships the persona mechanism; Phase 3 uses it.
- **a11y baseline** — Phase 3 D3.6 (`@axe-core/playwright`).
- **Mobile E2E expansion** — Phase 3 D3.7.
- **Mutation testing (mutmut)** — Phase 4 D4.3.
- **`bats` for scripts** — Phase 4 D4.1.
- **Chaos / perf / fuzzing** — Phase 5 (optional, trigger-gated).
- **Audit→Enforce chart values flag** (`kyverno.harborImageSignatureEnforce`) — separate spec, operator-decision issue #187 (2026-05-22). D2.5 keeps the chart at Audit and patches at runtime.
- **PSS `enforce=restricted` chart default** — separate operator-decision issue #186 (2026-05-18). D2.5 keeps the chart at baseline and labels at runtime.
- **Detector-build BuildKit cosign signing** — separate spec `docs/superpowers/plans/2026-05-15-kyverno-attestation-enforcement.md`. Out of scope for the test-architecture roadmap.
