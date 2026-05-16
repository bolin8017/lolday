"""D3.3 — dev-mode seed endpoint integration test.

The router registers unconditionally; the handler gates on AUTH_DEV_MODE
and returns 404 when off. This test exercises both branches.
"""

from __future__ import annotations

import uuid

import pytest
from app.config import settings
from app.models import DatasetConfig, Detector, DetectorVersion, Job
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
