"""Tests for GET /api/v1/models — list registered models."""

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
    - detectors: elf-rf (owner alice), elf-cnn (owner alice)
    - alice/elf-rf has v1 (public, Production), v2 (private, Staging)
    - bob/elf-rf has v1 (private, None)
    - alice/elf-cnn has v1 (public, Production)
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
    det_cnn = Detector(
        name="elf-cnn",
        display_name="ELF CNN",
        git_url="https://github.com/x/elf-cnn",
        owner_id=alice.id,
    )
    db_session.add_all([det_rf, det_cnn])
    await db_session.flush()

    dv_rf = DetectorVersion(
        detector_id=det_rf.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="x/elf-rf:v1",
        image_digest="sha256:" + "0" * 64,
    )
    dv_cnn = DetectorVersion(
        detector_id=det_cnn.id,
        git_tag="v1",
        git_sha="b" * 40,
        harbor_image="x/elf-cnn:v1",
        image_digest="sha256:" + "1" * 64,
    )
    db_session.add_all([dv_rf, dv_cnn])
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
    rm_alice_cnn = RegisteredModel(owner_id=alice.id, detector_id=det_cnn.id)
    db_session.add_all([rm_alice_rf, rm_bob_rf, rm_alice_cnn])
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
        (
            rm_alice_cnn,
            1,
            alice,
            dv_cnn,
            ModelVersionVisibility.PUBLIC,
            ModelVersionStage.PRODUCTION,
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
        "rm_alice_cnn": rm_alice_cnn,
    }


def _client_for(user: User) -> AsyncClient:
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
    c = AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-test-user-email": user.email},
    )
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_alice_sees_own_private_plus_public(populated):
    alice = populated["alice"]
    async with _client_for(alice) as client:
        resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    rows = resp.json()
    names = sorted(f"{r['owner']}/{r['name']}" for r in rows)
    assert names == ["alice/elf-cnn", "alice/elf-rf"]


async def test_bob_sees_alices_public_and_own_private(populated):
    bob = populated["bob"]
    async with _client_for(bob) as client:
        resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    rows = {f"{r['owner']}/{r['name']}": r for r in resp.json()}
    assert "alice/elf-rf" in rows  # has public v1
    assert "alice/elf-cnn" in rows  # all public
    assert "bob/elf-rf" in rows  # own private


async def test_alice_summary_counts_only_visible(populated):
    alice = populated["alice"]
    async with _client_for(alice) as client:
        resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    row = next(
        r for r in resp.json() if r["name"] == "elf-rf" and r["owner"] == "alice"
    )
    # alice sees both v1 (public) and v2 (private, owned)
    assert row["latest_version"] == 2
    assert row["latest_production_version"] == 1
    assert row["latest_staging_version"] == 2


async def test_bob_summary_counts_only_visible_in_alice_namespace(populated):
    bob = populated["bob"]
    async with _client_for(bob) as client:
        resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    row = next(
        r for r in resp.json() if r["name"] == "elf-rf" and r["owner"] == "alice"
    )
    # bob sees only v1 (public) of alice/elf-rf
    assert row["latest_version"] == 1
    assert row["latest_production_version"] == 1
    assert row["latest_staging_version"] is None


async def test_filter_owner(populated):
    alice = populated["alice"]
    async with _client_for(alice) as client:
        resp = await client.get("/api/v1/models?owner=bob")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows == []  # alice can't see bob's all-private model


async def test_filter_visibility_mine(populated):
    alice = populated["alice"]
    async with _client_for(alice) as client:
        resp = await client.get("/api/v1/models?visibility=mine")
    assert resp.status_code == 200
    rows = sorted(f"{r['owner']}/{r['name']}" for r in resp.json())
    assert rows == ["alice/elf-cnn", "alice/elf-rf"]


async def test_filter_visibility_public(populated):
    bob = populated["bob"]
    async with _client_for(bob) as client:
        resp = await client.get("/api/v1/models?visibility=public")
    assert resp.status_code == 200
    rows_names = [f"{r['owner']}/{r['name']}" for r in resp.json()]
    assert "alice/elf-cnn" in rows_names  # has public version
    assert "bob/elf-rf" not in rows_names  # only private


async def test_admin_sees_all(populated, db_session):
    admin = User(email="adm@x.com", handle="admin-acct", role=Role.ADMIN)
    db_session.add(admin)
    await db_session.commit()
    async with _client_for(admin) as client:
        resp = await client.get("/api/v1/models")
    assert resp.status_code == 200
    rows = sorted(f"{r['owner']}/{r['name']}" for r in resp.json())
    assert rows == ["alice/elf-cnn", "alice/elf-rf", "bob/elf-rf"]
