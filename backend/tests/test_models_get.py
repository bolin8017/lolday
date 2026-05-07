"""Tests for GET /api/v1/models/{owner}/{name}/... — namespaced detail endpoints."""

import uuid as _u

import pytest_asyncio
from app.models import (
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def populated(db_session):
    """Build the test universe.

    Universe:
    - alice (developer), bob (developer)
    - detectors: elf-rf (owner alice)
    - alice/elf-rf has v1 (public, Production), v2 (private, Staging)
    - bob/elf-rf has v1 (private, None)
    """
    alice = User(email="alice@x.com", handle="alice", role=Role.DEVELOPER)
    bob = User(email="bob@x.com", handle="bob", role=Role.DEVELOPER)
    db_session.add_all([alice, bob])
    await db_session.flush()

    det_rf = Detector(
        name="elf-rf",
        display_name="ELF RF",
        git_url="https://github.com/x/elf-rf",
        owner_id=alice.id,
    )
    db_session.add(det_rf)
    await db_session.flush()

    dv_rf = DetectorVersion(
        detector_id=det_rf.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="x/elf-rf:v1",
        image_digest="sha256:" + "0" * 64,
    )
    db_session.add(dv_rf)
    await db_session.flush()

    def _job(owner: User, dv: DetectorVersion) -> Job:
        return Job(
            type=JobType.TRAIN,
            owner_id=owner.id,
            detector_version_id=dv.id,
            status=JobStatus.SUCCEEDED,
            mlflow_run_id=_u.uuid4().hex,
            resolved_config={},
            idempotency_key=_u.uuid4().hex,
        )

    rm_alice_rf = RegisteredModel(owner_id=alice.id, detector_id=det_rf.id)
    rm_bob_rf = RegisteredModel(owner_id=bob.id, detector_id=det_rf.id)
    db_session.add_all([rm_alice_rf, rm_bob_rf])
    await db_session.flush()

    versions_to_make = [
        # (rm, version, owner, dv, visibility, stage)
        (
            rm_alice_rf,
            1,
            alice,
            dv_rf,
            ModelVersionVisibility.PUBLIC,
            ModelVersionStage.PRODUCTION,
        ),
        (
            rm_alice_rf,
            2,
            alice,
            dv_rf,
            ModelVersionVisibility.PRIVATE,
            ModelVersionStage.STAGING,
        ),
        (
            rm_bob_rf,
            1,
            bob,
            dv_rf,
            ModelVersionVisibility.PRIVATE,
            ModelVersionStage.NONE,
        ),
    ]
    for rm, ver, owner, dv, vis, stage in versions_to_make:
        j = _job(owner, dv)
        db_session.add(j)
        await db_session.flush()
        mv = ModelVersion(
            registered_model_id=rm.id,
            mlflow_version=ver,
            mlflow_run_id=j.mlflow_run_id,
            current_stage=stage,
            visibility=vis,
            detector_version_id=dv.id,
            source_job_id=j.id,
            owner_id=owner.id,
        )
        db_session.add(mv)
    await db_session.commit()

    return {
        "alice": alice,
        "bob": bob,
        "rm_alice_rf": rm_alice_rf,
        "rm_bob_rf": rm_bob_rf,
    }


def client_factory(user: User) -> AsyncClient:
    """Return an AsyncClient that authenticates as the given user (header-based)."""
    from app.db import get_async_session
    from app.main import app

    from tests.conftest import test_session_maker

    async def override():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override

    from app.auth.cf_access import cf_access_user
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    async def _fake_auth(
        request: Request,
        session: AsyncSession = Depends(get_async_session),
    ) -> User:
        email = request.headers.get("x-test-user-email")
        if not email:
            raise HTTPException(401, "missing X-Test-User-Email")
        row = (
            await session.execute(select(User).where(User.email == email))
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
# Tests
# ---------------------------------------------------------------------------


async def test_get_summary_owner_sees_private(populated):
    alice = populated["alice"]
    async with client_factory(alice) as client:
        resp = await client.get("/api/v1/models/alice/elf-rf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == "alice"
    assert body["name"] == "elf-rf"
    # alice sees v1 (public) + v2 (private, owned)
    assert body["latest_version"] == 2


async def test_get_summary_non_owner_only_public(populated):
    bob = populated["bob"]
    async with client_factory(bob) as client:
        resp = await client.get("/api/v1/models/alice/elf-rf")
    assert resp.status_code == 200
    body = resp.json()
    # bob sees only v1 (public)
    assert body["latest_version"] == 1


async def test_get_summary_404_for_all_private_other_user(populated):
    alice = populated["alice"]
    async with client_factory(alice) as client:
        # alice tries to read bob/elf-rf which is fully private
        resp = await client.get("/api/v1/models/bob/elf-rf")
    assert resp.status_code == 404


async def test_list_versions_filters_private_for_non_owner(populated):
    bob = populated["bob"]
    async with client_factory(bob) as client:
        resp = await client.get("/api/v1/models/alice/elf-rf/versions")
    assert resp.status_code == 200
    versions = [v["mlflow_version"] for v in resp.json()["items"]]
    assert versions == [1]  # bob doesn't see v2 (private)


async def test_get_version_owner_sees_private(populated):
    alice = populated["alice"]
    async with client_factory(alice) as client:
        resp = await client.get("/api/v1/models/alice/elf-rf/versions/2")
    assert resp.status_code == 200


async def test_get_version_non_owner_404_private(populated):
    bob = populated["bob"]
    async with client_factory(bob) as client:
        resp = await client.get("/api/v1/models/alice/elf-rf/versions/2")
    assert resp.status_code == 404
