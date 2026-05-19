"""Tests for ``app.reconciler.model_sync.sync_model_versions``.

``sync_model_versions`` runs every ~60s from ``reconciler_loop`` and
mirrors stage transitions made via the MLflow UI back into the lolday
DB so the in-app UI shows the current ``current_stage``. The module
sits at 26% coverage on ``main`` because no test ever exercised it
directly — the function path is small but every branch matters:

- Early return when the DB has no local model versions (no MLflow call).
- Skip a local row when the remote search has no matching ``(name,
  version)`` tuple (deleted-on-MLflow / never-pushed case).
- Skip a local row when the remote stage is not a valid
  ``ModelVersionStage`` enum value (forward-compat against an MLflow
  stage rename).
- No-op when the remote stage equals the local stage.
- Update ``current_stage`` + ``last_transitioned_at`` when the remote
  stage differs.
- ``mlflow=None`` constructor fallback (legacy test call-sites).

The tests use the integration-tier ``db_session`` fixture and a hand-
rolled stub for ``MlflowClient.search_model_versions`` so we control
the remote payload exactly. The autouse ``mock_mlflow`` stub returns
``[]`` for ``search_model_versions``, which is fine for the
"early return" case but not for the others.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa
from app.models import (
    Detector,
    DetectorVersion,
    Job,
    JobStatus,
    JobType,
    ModelVersion,
    RegisteredModel,
    Role,
    User,
)
from app.models.model_registry import ModelVersionStage, ModelVersionVisibility
from app.reconciler.model_sync import sync_model_versions


class _StubMlflow:
    """Minimal MlflowClient stand-in. Only ``search_model_versions`` is
    used by ``sync_model_versions``."""

    def __init__(self, versions: list[dict[str, Any]] | None = None) -> None:
        self.versions = versions or []
        self.calls = 0

    async def search_model_versions(
        self, filter_string: str | None = None, max_results: int = 200
    ) -> list[dict[str, Any]]:
        self.calls += 1
        return self.versions


@pytest.fixture
async def alice_train_mv(db_session):
    """Seed (alice, elf-rf) with one DetectorVersion, a SUCCEEDED train
    Job, a RegisteredModel, and a single ``ModelVersion`` at stage
    ``NONE``. Returns the ORM objects so individual tests can assert
    against them after the sync runs.

    The mlflow_name property of the RegisteredModel resolves to
    ``"alice/elf-rf"`` — every test passes its remote search result
    under that key.
    """
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
    job = Job(
        type=JobType.TRAIN,
        owner_id=alice.id,
        detector_version_id=dv.id,
        status=JobStatus.SUCCEEDED,
        mlflow_run_id="r-alice-1",
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
    )
    db_session.add(job)
    await db_session.flush()
    rm = RegisteredModel(owner_id=alice.id, detector_id=det.id, tags={})
    db_session.add(rm)
    await db_session.flush()
    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=1,
        mlflow_run_id="r-alice-1",
        current_stage=ModelVersionStage.NONE,
        visibility=ModelVersionVisibility.PRIVATE,
        detector_version_id=dv.id,
        source_job_id=job.id,
        owner_id=alice.id,
    )
    db_session.add(mv)
    await db_session.commit()
    return {"alice": alice, "rm": rm, "mv": mv}


async def test_early_return_when_no_local_versions(db_session):
    """No ``ModelVersion`` rows → never touches MLflow, commits cleanly."""
    stub = _StubMlflow(
        versions=[{"name": "x", "version": 1, "current_stage": "Staging"}]
    )
    await sync_model_versions(db_session, mlflow=stub)
    assert stub.calls == 0, "should short-circuit before hitting MLflow"


async def test_promotes_local_stage_when_remote_advances(alice_train_mv, db_session):
    """The MLflow UI promoted v1 None → Staging. The next sync iteration
    must reflect that into the lolday DB, refresh
    ``last_transitioned_at``, and commit."""
    actors = alice_train_mv
    # Pin last_transitioned_at to a known past instant. SQLite stores
    # the column as a naive datetime; the production Postgres path is
    # tz-aware. We use a naive past value here so the comparison is
    # apples-to-apples on the test DB (Postgres heavy-tier already
    # covers the tz-aware path implicitly).
    pinned = datetime(2025, 1, 1, 0, 0, 0)
    actors["mv"].last_transitioned_at = pinned
    await db_session.commit()

    stub = _StubMlflow(
        versions=[{"name": "alice/elf-rf", "version": "1", "current_stage": "Staging"}]
    )
    await sync_model_versions(db_session, mlflow=stub)

    refreshed = (
        await db_session.execute(
            sa.select(ModelVersion).where(ModelVersion.id == actors["mv"].id)
        )
    ).scalar_one()
    assert refreshed.current_stage == ModelVersionStage.STAGING
    # The sync writes ``datetime.now(UTC)``; the column came back through
    # SQLAlchemy with the same tz-awareness it was assigned with. We only
    # care that the timestamp actually moved off the pinned past value.
    assert refreshed.last_transitioned_at != pinned


async def test_remote_matches_local_stage_is_noop(alice_train_mv, db_session):
    """Remote stage equals local stage → ``current_stage`` and
    ``last_transitioned_at`` both untouched. We pin
    ``last_transitioned_at`` by setting it to a fixed past instant
    before the sync and asserting equality afterwards.
    """
    actors = alice_train_mv
    pinned = datetime(2025, 1, 1, 0, 0, 0)
    actors["mv"].last_transitioned_at = pinned
    await db_session.commit()

    stub = _StubMlflow(
        versions=[{"name": "alice/elf-rf", "version": "1", "current_stage": "None"}]
    )
    await sync_model_versions(db_session, mlflow=stub)

    refreshed = (
        await db_session.execute(
            sa.select(ModelVersion).where(ModelVersion.id == actors["mv"].id)
        )
    ).scalar_one()
    assert refreshed.current_stage == ModelVersionStage.NONE
    assert refreshed.last_transitioned_at == pinned


async def test_unknown_remote_stage_is_skipped(alice_train_mv, db_session):
    """MLflow returns a stage string outside the lolday ``ModelVersionStage``
    enum (e.g. a future MLflow stage rename). The row is skipped without
    raising, leaving the local row at its current stage."""
    actors = alice_train_mv
    stub = _StubMlflow(
        versions=[
            {"name": "alice/elf-rf", "version": "1", "current_stage": "Champion"},
        ]
    )
    await sync_model_versions(db_session, mlflow=stub)

    refreshed = (
        await db_session.execute(
            sa.select(ModelVersion).where(ModelVersion.id == actors["mv"].id)
        )
    ).scalar_one()
    assert refreshed.current_stage == ModelVersionStage.NONE


async def test_local_row_with_no_remote_match_is_skipped(alice_train_mv, db_session):
    """Remote search returns a different (name, version) tuple — local
    row has no remote counterpart (deleted on MLflow side, or never
    pushed). Row is skipped silently; no exception, no stage change."""
    actors = alice_train_mv
    stub = _StubMlflow(
        versions=[{"name": "other/det", "version": "1", "current_stage": "Staging"}]
    )
    await sync_model_versions(db_session, mlflow=stub)

    refreshed = (
        await db_session.execute(
            sa.select(ModelVersion).where(ModelVersion.id == actors["mv"].id)
        )
    ).scalar_one()
    assert refreshed.current_stage == ModelVersionStage.NONE


async def test_mlflow_none_falls_back_to_default_constructor(
    alice_train_mv, db_session, monkeypatch
):
    """The ``mlflow=None`` fallback constructs ``MlflowClient(...)``
    with a fresh ``httpx.AsyncClient``. Patch the constructor so the
    test never reaches a real MLflow server, and assert the constructor
    was invoked with ``settings.MLFLOW_TRACKING_URI``.
    """
    constructed: list[dict[str, Any]] = []

    class _StubMlflowCtor(_StubMlflow):
        def __init__(self, tracking_uri: str, http_client: Any) -> None:
            super().__init__(
                versions=[
                    {
                        "name": "alice/elf-rf",
                        "version": "1",
                        "current_stage": "Production",
                    }
                ]
            )
            constructed.append({"uri": tracking_uri, "http": http_client})

    monkeypatch.setattr("app.reconciler.model_sync.MlflowClient", _StubMlflowCtor)
    await sync_model_versions(db_session, mlflow=None)

    refreshed = (
        await db_session.execute(
            sa.select(ModelVersion).where(ModelVersion.id == alice_train_mv["mv"].id)
        )
    ).scalar_one()
    assert refreshed.current_stage == ModelVersionStage.PRODUCTION
    assert len(constructed) == 1
    from app.config import settings

    assert constructed[0]["uri"] == settings.MLFLOW_TRACKING_URI
