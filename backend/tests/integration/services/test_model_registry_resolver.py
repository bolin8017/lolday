"""Unit tests for resolve_registered_model access control helper."""

import uuid

import pytest
from app.models import (
    Detector,
    DetectorVersion,
    Job,
    JobStatus,
    JobType,
    ModelVersion,
    ModelVersionVisibility,
    RegisteredModel,
    Role,
    User,
)
from app.services.model_registry import resolve_registered_model
from fastapi import HTTPException


@pytest.fixture
async def setup_actors(db_session):
    """Create three users + a Detector with one DetectorVersion + a Job (template fixtures)."""
    alice = User(email="alice@x.com", handle="alice", role=Role.DEVELOPER)
    bob = User(email="bob@x.com", handle="bob", role=Role.DEVELOPER)
    admin = User(email="adm@x.com", handle="admin-acct", role=Role.ADMIN)
    db_session.add_all([alice, bob, admin])
    await db_session.flush()
    det = Detector(
        name="elf-rf",
        display_name="ELF RF",
        git_url="https://github.com/x/y",
        owner_id=alice.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1.0.0",
        git_sha="a" * 40,
        harbor_image="harbor.harbor.svc:80/detectors/elf-rf:v1.0.0",
        image_digest="sha256:" + "a" * 64,
    )
    db_session.add(dv)
    await db_session.flush()
    job = Job(
        type=JobType.TRAIN,
        owner_id=alice.id,
        detector_version_id=dv.id,
        status=JobStatus.SUCCEEDED,
        mlflow_run_id="r-1",
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    db_session.add(job)
    await db_session.flush()
    return {
        "alice": alice,
        "bob": bob,
        "admin": admin,
        "det": det,
        "dv": dv,
        "job": job,
    }


async def _make_rm_with_one_version(
    db_session, *, owner, detector, dv, job, visibility
):
    rm = RegisteredModel(owner_id=owner.id, detector_id=detector.id)
    db_session.add(rm)
    await db_session.flush()
    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=1,
        mlflow_run_id="r-1",
        visibility=visibility,
        detector_version_id=dv.id,
        source_job_id=job.id,
        owner_id=owner.id,
    )
    db_session.add(mv)
    await db_session.flush()
    return rm


async def test_owner_read_succeeds(db_session, setup_actors):
    actors = setup_actors
    rm = await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PRIVATE,
    )
    out = await resolve_registered_model("alice", "elf-rf", db_session, actors["alice"])
    assert out.id == rm.id


async def test_non_owner_404_when_all_private(db_session, setup_actors):
    actors = setup_actors
    await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PRIVATE,
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model("alice", "elf-rf", db_session, actors["bob"])
    assert exc.value.status_code == 404


async def test_non_owner_200_when_any_public(db_session, setup_actors):
    actors = setup_actors
    await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PUBLIC,
    )
    out = await resolve_registered_model("alice", "elf-rf", db_session, actors["bob"])
    assert out is not None


async def test_admin_sees_private(db_session, setup_actors):
    actors = setup_actors
    await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PRIVATE,
    )
    out = await resolve_registered_model("alice", "elf-rf", db_session, actors["admin"])
    assert out is not None


async def test_write_non_owner_403(db_session, setup_actors):
    actors = setup_actors
    await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PUBLIC,
    )
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model(
            "alice", "elf-rf", db_session, actors["bob"], write=True
        )
    assert exc.value.status_code == 403


async def test_write_admin_succeeds(db_session, setup_actors):
    actors = setup_actors
    await _make_rm_with_one_version(
        db_session,
        owner=actors["alice"],
        detector=actors["det"],
        dv=actors["dv"],
        job=actors["job"],
        visibility=ModelVersionVisibility.PUBLIC,
    )
    out = await resolve_registered_model(
        "alice", "elf-rf", db_session, actors["admin"], write=True
    )
    assert out is not None


async def test_404_when_model_not_found(db_session, setup_actors):
    actors = setup_actors
    with pytest.raises(HTTPException) as exc:
        await resolve_registered_model(
            "alice", "nonexistent-detector", db_session, actors["alice"]
        )
    assert exc.value.status_code == 404
