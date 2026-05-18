"""D3.3 — dev-mode seed endpoint integration test.

The router registers unconditionally; the handler gates on AUTH_DEV_MODE
and returns 404 when off. This test exercises both branches.
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import (
    DatasetConfig,
    Detector,
    DetectorVersion,
    Job,
    ModelVersion,
    ModelVersionVisibility,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_seed_fixtures_404_when_dev_mode_off(
    auth_client_admin: AsyncClient,
) -> None:
    """Production-mode (AUTH_DEV_MODE=False) returns 404."""
    assert not settings.AUTH_DEV_MODE  # default
    resp = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert resp.status_code == 404, resp.text


async def test_seed_fixtures_creates_deterministic_rows(
    auth_client_admin: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With AUTH_DEV_MODE on, first call seeds; second is a no-op returning the same IDs."""
    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)

    first = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert first.status_code == 200, first.text
    body_a = first.json()
    assert body_a["detector_id"]
    assert body_a["detector_version_id"]
    assert body_a["train_dataset_id"]
    assert body_a["test_dataset_id"]
    assert body_a["queued_job_id"]
    assert body_a["registered_model_id"]
    assert body_a["model_version_id"]

    second = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert second.status_code == 200
    body_b = second.json()
    assert body_a == body_b  # idempotent


async def test_seed_fixtures_inserts_rows_we_can_read_back(
    auth_client_admin: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)

    resp = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert resp.status_code == 200
    body = resp.json()

    detector = await db_session.get(Detector, uuid.UUID(body["detector_id"]))
    assert detector is not None
    assert detector.name
    version = await db_session.get(
        DetectorVersion, uuid.UUID(body["detector_version_id"])
    )
    assert version is not None
    assert str(version.detector_id) == body["detector_id"]
    train_ds = await db_session.get(DatasetConfig, uuid.UUID(body["train_dataset_id"]))
    assert train_ds is not None
    test_ds = await db_session.get(DatasetConfig, uuid.UUID(body["test_dataset_id"]))
    assert test_ds is not None
    job = await db_session.get(Job, uuid.UUID(body["queued_job_id"]))
    assert job is not None


async def test_seed_fixtures_manifest_shape_compatible_with_resolve_detector_defaults(
    auth_client_admin: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the seeded DetectorVersion.manifest must satisfy the
    `resolve_detector_defaults` read path. A previous fixture shape used a
    list at `manifest.stages` (the LifecycleConfig lookalike) and crashed
    every E2E spec that rendered the seeded job with
    `AttributeError: 'list' object has no attribute 'get'`. Lock the dict
    shape so the next drift fails this test, not the playwright suite.
    """
    from app.models.job import JobType
    from app.services.jobs_params_validate import resolve_detector_defaults

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    resp = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert resp.status_code == 200
    version = await db_session.get(
        DetectorVersion, uuid.UUID(resp.json()["detector_version_id"])
    )
    assert version is not None
    for job_type in (JobType.TRAIN, JobType.EVALUATE, JobType.PREDICT):
        # Must not raise. Each stage carries a minimal `params_schema`
        # (single optional integer with default) so submit-path E2E specs
        # get past the "no params schema" guard. `resolve_detector_defaults`
        # extracts the defaults block — `{"batch_size": 32}` for every stage.
        assert resolve_detector_defaults(version.manifest, job_type) == {
            "batch_size": 32
        }


async def test_seed_fixtures_model_version_is_public(
    auth_client_admin: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seeded ModelVersion must be PUBLIC.

    The `GET /api/v1/models` LIST query inner-joins on ModelVersion with
    a visibility filter — for non-admin personas, only PUBLIC versions
    (or versions the caller owns) make the model row visible. The seed
    is shared across all personas, so PUBLIC is the only setting that
    lets the post-transfer `developer` see the model in their list view.

    Regression guard for PR #288: PRIVATE breaks
    `frontend/tests/e2e/models/transfer-and-delete.spec.ts` (the dev
    list assertion times out at `toBeVisible`). The playwright suite
    catches the same break in ~7s, but pytest catches it in <1s and
    points at the exact line.
    """
    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    resp = await auth_client_admin.post("/api/v1/dev/seed-fixtures")
    assert resp.status_code == 200, resp.text
    mv = await db_session.get(ModelVersion, uuid.UUID(resp.json()["model_version_id"]))
    assert mv is not None
    assert mv.visibility == ModelVersionVisibility.PUBLIC, (
        f"expected PUBLIC for cross-persona visibility, got {mv.visibility}"
    )
