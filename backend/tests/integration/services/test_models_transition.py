"""Tests for POST /api/v1/models/{owner}/{name}/versions/{version}/transition."""

import uuid as _u

import pytest_asyncio
import sqlalchemy as sa
from app.models import (
    Detector,
    DetectorVersion,
    Job,
    JobStatus,
    JobType,
    ModelTransitionLog,
    ModelVersion,
    ModelVersionStage,
    ModelVersionVisibility,
    RegisteredModel,
    Role,
    User,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def populated(db_session):
    """Build the test universe.

    Universe:
    - alice (developer), bob (developer)
    - detector: elf-rf (owner alice)
    - alice/elf-rf has v1 (public, None) and v2 (public, None)
    - bob/elf-rf has v1 (private, None)
    """
    alice = User(email="alice@trans.test", handle="alice-t", role=Role.DEVELOPER)
    bob = User(email="bob@trans.test", handle="bob-t", role=Role.DEVELOPER)
    db_session.add_all([alice, bob])
    await db_session.flush()

    det = Detector(
        name="elf-rf",
        display_name="ELF RF",
        git_url="https://github.com/x/elf-rf",
        owner_id=alice.id,
    )
    db_session.add(det)
    await db_session.flush()

    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="a" * 40,
        harbor_image="x/elf-rf:v1",
        image_digest="sha256:" + "0" * 64,
    )
    db_session.add(dv)
    await db_session.flush()

    def _job(owner: User) -> Job:
        return Job(
            type=JobType.TRAIN,
            owner_id=owner.id,
            detector_version_id=dv.id,
            status=JobStatus.SUCCEEDED,
            mlflow_run_id=_u.uuid4().hex,
            resolved_config={},
            idempotency_key=_u.uuid4().hex,
        )

    rm_alice = RegisteredModel(owner_id=alice.id, detector_id=det.id)
    rm_bob = RegisteredModel(owner_id=bob.id, detector_id=det.id)
    db_session.add_all([rm_alice, rm_bob])
    await db_session.flush()

    versions_to_make = [
        # (rm, mlflow_version, owner)
        (rm_alice, 1, alice),
        (rm_alice, 2, alice),
        (rm_bob, 1, bob),
    ]
    for rm, ver, owner in versions_to_make:
        j = _job(owner)
        db_session.add(j)
        await db_session.flush()
        mv = ModelVersion(
            registered_model_id=rm.id,
            mlflow_version=ver,
            mlflow_run_id=j.mlflow_run_id,
            current_stage=ModelVersionStage.NONE,
            visibility=ModelVersionVisibility.PUBLIC,
            detector_version_id=dv.id,
            source_job_id=j.id,
            owner_id=owner.id,
        )
        db_session.add(mv)
    await db_session.commit()

    return {"alice": alice, "bob": bob, "rm_alice": rm_alice, "rm_bob": rm_bob}


def client_factory(user: User) -> AsyncClient:
    """Return an AsyncClient that authenticates as the given user (header-based)."""
    from app.auth.cf_access import cf_access_user
    from app.db import get_async_session
    from app.main import app
    from fastapi import Depends, HTTPException, Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.conftest import test_session_maker

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


async def test_transition_forward_success(populated, mock_mlflow):
    """Owner can transition None → Staging."""
    alice = populated["alice"]
    async with client_factory(alice) as client:
        resp = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Staging", "comment": "going staging"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_stage"] == "Staging"


async def test_transition_to_production_auto_archives_existing(populated, mock_mlflow):
    """Promoting v2 to Production auto-archives the existing Production v1."""
    from tests.conftest import test_session_maker

    alice = populated["alice"]
    rm = populated["rm_alice"]

    # First: promote v1 to Production
    async with client_factory(alice) as client:
        r1 = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Production", "comment": "first prod"},
        )
        assert r1.status_code == 200

        # Then: promote v2 to Production — v1 must be auto-archived
        r2 = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/2/transition",
            json={"to_stage": "Production", "comment": "newer prod"},
        )
        assert r2.status_code == 200

    # Verify v1 is now Archived in the DB — use a fresh session to avoid stale identity map
    async with test_session_maker() as fresh_session:
        v1 = (
            await fresh_session.execute(
                sa.select(ModelVersion).where(
                    ModelVersion.registered_model_id == rm.id,
                    ModelVersion.mlflow_version == 1,
                )
            )
        ).scalar_one()
        assert v1.current_stage == ModelVersionStage.ARCHIVED


async def test_transition_denied_non_owner_developer(populated, mock_mlflow):
    """A different developer (bob) cannot transition alice's model."""
    bob = populated["bob"]
    async with client_factory(bob) as client:
        resp = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Staging"},
        )
    # 403 from resolve_registered_model (write=True, non-owner) or validate_transition
    assert resp.status_code in (403, 404)


async def test_transition_archived_to_production_denied(populated, mock_mlflow):
    """validate_transition: Archived → Production is invalid for any non-admin role."""
    alice = populated["alice"]
    # Transition v1 to Archived directly (None → Archived is valid)
    async with client_factory(alice) as client:
        r1 = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Archived"},
        )
        assert r1.status_code == 200

        r2 = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Production"},
        )
    assert r2.status_code == 403


async def test_transition_writes_audit_log(populated, db_session, mock_mlflow):
    """A successful transition writes a ModelTransitionLog row."""
    alice = populated["alice"]
    async with client_factory(alice) as client:
        await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Staging", "comment": "audit-log-test"},
        )

    logs = (await db_session.execute(sa.select(ModelTransitionLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].to_stage.value == "Staging"
    assert logs[0].comment == "audit-log-test"


async def test_transition_auto_archive_writes_audit_logs(
    populated, db_session, mock_mlflow
):
    """Auto-archiving existing Production siblings writes audit logs for them too."""
    alice = populated["alice"]
    async with client_factory(alice) as client:
        await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/1/transition",
            json={"to_stage": "Production"},
        )
        await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/2/transition",
            json={"to_stage": "Production"},
        )

    logs = (
        (
            await db_session.execute(
                sa.select(ModelTransitionLog).order_by(
                    ModelTransitionLog.transitioned_at
                )
            )
        )
        .scalars()
        .all()
    )
    # Expect: v1 → Production, v1 auto-archived (by v2 promotion), v2 → Production
    assert len(logs) == 3
    archived_auto = [
        lg for lg in logs if lg.comment == "auto-archived by transition to Production"
    ]
    assert len(archived_auto) == 1
    assert archived_auto[0].to_stage == ModelVersionStage.ARCHIVED


async def test_transition_version_not_found_404(populated, mock_mlflow):
    """Requesting transition on non-existent version → 404."""
    alice = populated["alice"]
    async with client_factory(alice) as client:
        resp = await client.post(
            "/api/v1/models/alice-t/elf-rf/versions/999/transition",
            json={"to_stage": "Staging"},
        )
    assert resp.status_code == 404
