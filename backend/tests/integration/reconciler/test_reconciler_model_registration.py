"""Tests for _register_model_from_job reconciler logic."""

import uuid

import pytest
import sqlalchemy as sa
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
from app.reconciler.jobs import _register_model_from_job


@pytest.fixture
async def alice_with_detector(db_session):
    alice = User(email="alice@x.com", handle="alice", role=Role.DEVELOPER)
    db_session.add(alice)
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
    return {"alice": alice, "det": det, "dv": dv}


def _make_job(owner: User, dv: DetectorVersion, mlflow_run_id: str) -> Job:
    return Job(
        type=JobType.TRAIN,
        owner_id=owner.id,
        detector_version_id=dv.id,
        status=JobStatus.SUCCEEDED,
        mlflow_run_id=mlflow_run_id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )


async def test_first_train_creates_registered_model(
    alice_with_detector, db_session, mock_mlflow
):
    actors = alice_with_detector
    j = _make_job(actors["alice"], actors["dv"], "r1")
    db_session.add(j)
    await db_session.flush()

    client = mock_mlflow
    await _register_model_from_job(db_session, client, j)
    await db_session.commit()

    rm = (
        await db_session.execute(
            sa.select(RegisteredModel).where(
                RegisteredModel.owner_id == actors["alice"].id,
                RegisteredModel.detector_id == actors["det"].id,
            )
        )
    ).scalar_one()
    assert rm is not None

    mv = (
        await db_session.execute(
            sa.select(ModelVersion).where(
                ModelVersion.registered_model_id == rm.id,
            )
        )
    ).scalar_one()
    assert mv.visibility == ModelVersionVisibility.PRIVATE
    assert mv.owner_id == actors["alice"].id


async def test_second_train_reuses_registered_model(
    alice_with_detector, db_session, mock_mlflow
):
    actors = alice_with_detector
    client = mock_mlflow

    j1 = _make_job(actors["alice"], actors["dv"], "r1")
    db_session.add(j1)
    await db_session.flush()
    await _register_model_from_job(db_session, client, j1)

    j2 = _make_job(actors["alice"], actors["dv"], "r2")
    db_session.add(j2)
    await db_session.flush()
    await _register_model_from_job(db_session, client, j2)

    await db_session.commit()

    # Only ONE RegisteredModel for (alice, elf-rf)
    rms = (
        (
            await db_session.execute(
                sa.select(RegisteredModel).where(
                    RegisteredModel.owner_id == actors["alice"].id,
                    RegisteredModel.detector_id == actors["det"].id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rms) == 1


async def test_two_users_train_same_detector_get_separate_namespaces(
    alice_with_detector, db_session, mock_mlflow
):
    actors = alice_with_detector
    bob = User(email="bob@x.com", handle="bob", role=Role.DEVELOPER)
    db_session.add(bob)
    await db_session.flush()
    client = mock_mlflow

    j_alice = _make_job(actors["alice"], actors["dv"], "r-alice")
    j_bob = _make_job(bob, actors["dv"], "r-bob")
    db_session.add_all([j_alice, j_bob])
    await db_session.flush()

    await _register_model_from_job(db_session, client, j_alice)
    await _register_model_from_job(db_session, client, j_bob)
    await db_session.commit()

    rms = (await db_session.execute(sa.select(RegisteredModel))).scalars().all()
    owner_ids = sorted(r.owner_id for r in rms)
    assert len(rms) == 2
    assert actors["alice"].id in owner_ids
    assert bob.id in owner_ids

    # MLflow create_registered_model should have been called with both namespaces
    assert "alice/elf-rf" in mock_mlflow.create_registered_model_calls
    assert "bob/elf-rf" in mock_mlflow.create_registered_model_calls
