"""Tests for predict-job source_model_version_id visibility gate.

Validates the ``_load_model_version_for_predict`` helper introduced in
``app/routers/jobs.py``:

- non-owner cannot use a private ModelVersion (→ 422 "not accessible")
- any user can use a public ModelVersion (→ 202 or non-access 422)
- Section 1.4 reverted: any user can train against any detector (→ NOT 403)
"""

import uuid as _u
from pathlib import Path

import pytest_asyncio
from app.models import (
    DatasetConfig,
    DatasetVisibility,
    Detector,
    DetectorVersion,
    Job,
    JobStatus,
    JobType,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    RegisteredModel,
    Role,
    User,
)
from httpx import ASGITransport, AsyncClient

from tests.conftest import _MINIMAL_MANIFEST, test_session_maker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_CSV = (Path(__file__).parent / "fixtures" / "sample_dataset.csv").read_text()


def _client_for(user: User) -> AsyncClient:
    """Return an AsyncClient that authenticates as ``user`` (header-based)."""
    from app.auth.cf_access import cf_access_user
    from app.db import get_async_session
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override

    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email")
        row = (
            await session.execute(_select(User).where(User.email == email))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(401, f"test fixture: user not seeded: {email}")
        return row

    app.dependency_overrides[cf_access_user] = _fake_auth
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-test-user-email": user.email},
    )


# ---------------------------------------------------------------------------
# Fixture: universe
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def universe(db_session):
    """Seed a minimal universe for visibility tests.

    - alice (DEVELOPER): owns elf-rf detector + v1/v2
      - mv v1: PUBLIC, Production
      - mv v2: PRIVATE, Staging
    - bob (DEVELOPER): no model versions
    - dv_rf has _MINIMAL_MANIFEST so POST /jobs manifest pre-flight passes
    - public_ds: PUBLIC DatasetConfig owned by alice (predict_dataset_id for bob)
    """
    alice = User(email="alice-vis@x.com", handle="alice-vis", role=Role.DEVELOPER)
    bob = User(email="bob-vis@x.com", handle="bob-vis", role=Role.DEVELOPER)
    db_session.add_all([alice, bob])
    await db_session.flush()

    det_rf = Detector(
        name="elf-rf-vis",
        display_name="ELF RF Vis",
        git_url="https://github.com/x/elf-rf-vis",
        owner_id=alice.id,
    )
    db_session.add(det_rf)
    await db_session.flush()

    dv_rf = DetectorVersion(
        detector_id=det_rf.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="x/elf-rf-vis:v1",
        image_digest="sha256:" + "0" * 64,
        manifest=_MINIMAL_MANIFEST,
    )
    db_session.add(dv_rf)
    await db_session.flush()

    # jobs needed as source_job_id for ModelVersion
    def _job(owner: User) -> Job:
        return Job(
            type=JobType.TRAIN,
            owner_id=owner.id,
            detector_version_id=dv_rf.id,
            status=JobStatus.SUCCEEDED,
            mlflow_run_id=_u.uuid4().hex,
            resolved_config={},
            idempotency_key=_u.uuid4().hex,
        )

    j1 = _job(alice)
    j2 = _job(alice)
    db_session.add_all([j1, j2])
    await db_session.flush()

    rm_alice_rf = RegisteredModel(owner_id=alice.id, detector_id=det_rf.id)
    db_session.add(rm_alice_rf)
    await db_session.flush()

    mv_public = ModelVersion(
        registered_model_id=rm_alice_rf.id,
        mlflow_version=1,
        mlflow_run_id=j1.mlflow_run_id,
        current_stage=ModelVersionStage.PRODUCTION,
        visibility=ModelVersionVisibility.PUBLIC,
        detector_version_id=dv_rf.id,
        source_job_id=j1.id,
        owner_id=alice.id,
    )
    mv_private = ModelVersion(
        registered_model_id=rm_alice_rf.id,
        mlflow_version=2,
        mlflow_run_id=j2.mlflow_run_id,
        current_stage=ModelVersionStage.STAGING,
        visibility=ModelVersionVisibility.PRIVATE,
        detector_version_id=dv_rf.id,
        source_job_id=j2.id,
        owner_id=alice.id,
    )
    db_session.add_all([mv_public, mv_private])

    # A PUBLIC dataset owned by alice that bob can use as predict_dataset_id
    checksum = "0" * 64
    public_ds = DatasetConfig(
        name="alice-public-ds",
        owner_id=alice.id,
        visibility=DatasetVisibility.PUBLIC,
        csv_content=FIXTURE_CSV,
        csv_checksum=checksum,
        sample_count=5,
        size_bytes=len(FIXTURE_CSV.encode()),
    )
    db_session.add(public_ds)

    await db_session.commit()
    await db_session.refresh(mv_public)
    await db_session.refresh(mv_private)
    await db_session.refresh(public_ds)

    return {
        "alice": alice,
        "bob": bob,
        "dv_rf": dv_rf,
        "mv_public": mv_public,
        "mv_private": mv_private,
        "public_ds": public_ds,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_predict_with_private_model_non_owner_422(universe):
    """Non-owner bob cannot use alice's private ModelVersion (→ 422 not accessible)."""
    bob = universe["bob"]
    dv_rf = universe["dv_rf"]
    mv_private = universe["mv_private"]
    public_ds = universe["public_ds"]

    async with _client_for(bob) as c:
        resp = await c.post(
            "/api/v1/jobs",
            json={
                "type": "predict",
                "detector_version_id": str(dv_rf.id),
                "source_model_version_id": str(mv_private.id),
                "predict_dataset_id": str(public_ds.id),
                "params": {},
            },
        )

    assert resp.status_code == 422, resp.text
    assert "not accessible" in resp.json()["detail"]


async def test_predict_with_public_model_any_user_not_403(universe):
    """Bob using alice's PUBLIC model must NOT get 403 (visibility gate must not block it)."""
    bob = universe["bob"]
    dv_rf = universe["dv_rf"]
    mv_public = universe["mv_public"]
    public_ds = universe["public_ds"]

    async with _client_for(bob) as c:
        resp = await c.post(
            "/api/v1/jobs",
            json={
                "type": "predict",
                "detector_version_id": str(dv_rf.id),
                "source_model_version_id": str(mv_public.id),
                "predict_dataset_id": str(public_ds.id),
                "params": {},
            },
        )

    # 202 = accepted; anything besides 403 / "not accessible" means the
    # visibility gate did not wrongly block the request.
    assert resp.status_code != 403, resp.text
    if resp.status_code == 422:
        assert "not accessible" not in resp.json().get("detail", "")


async def test_train_against_any_detector_no_403(universe):
    """Section 1.4 reverted: bob can train using alice's detector_version_id (→ NOT 403)."""
    bob = universe["bob"]
    dv_rf = universe["dv_rf"]
    public_ds = universe["public_ds"]

    async with _client_for(bob) as c:
        resp = await c.post(
            "/api/v1/jobs",
            json={
                "type": "train",
                "detector_version_id": str(dv_rf.id),
                "train_dataset_id": str(public_ds.id),
                "test_dataset_id": str(public_ds.id),
                "params": {},
            },
        )

    # Must NOT be 403 — detector ownership gate was reverted
    assert resp.status_code != 403, resp.text
