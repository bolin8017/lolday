"""Phase 11b reconciler: trust stage_end event before Volcano phase."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.models import (
    Detector,
    DetectorVersion,
    Job,
    JobEvent,
    JobStatus,
    User,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_job_running(session: AsyncSession) -> Job:
    _uid = uuid.uuid4()
    user = User(
        id=_uid,
        email=f"rec-{_uid.hex[:8]}@example.com",
        handle=f"rec-{_uid.hex[:8]}",
    )
    session.add(user)
    await session.flush()  # user must be persisted before Detector FK can reference it
    det = Detector(
        name=f"rec-det-{uuid.uuid4().hex[:8]}",
        display_name="rec",
        owner_id=user.id,
        git_url="https://example.com/r.git",
    )
    session.add(det)
    await session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v1",
        git_sha="deadbeef",
        harbor_image="h/x:v1",
        image_digest="sha256:abc",
    )
    session.add(dv)
    await session.flush()
    job = Job(
        type="train",
        status=JobStatus.RUNNING,
        owner_id=user.id,
        detector_version_id=dv.id,
        resolved_config={},
        idempotency_key=uuid.uuid4().hex,
        k8s_job_name="job-train-abc12345",
        started_at=datetime.now(UTC),
        mlflow_run_id="r1",
    )
    session.add(job)
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_reconcile_trusts_stage_end_success(db_session, monkeypatch) -> None:
    """When the most-recent stage_end event has status=success, mark job SUCCEEDED
    even if the Volcano phase is still Running."""
    from app.reconciler import reconcile_job

    job = await _seed_job_running(db_session)

    # Seed a stage_end event with success
    db_session.add(
        JobEvent(
            job_id=job.id,
            ts=datetime.now(UTC),
            kind="stage_end",
            payload={"stage": "train", "status": "success"},
        )
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_succeeded(session, j):
        calls.append("succeeded")
        j.status = JobStatus.SUCCEEDED

    async def fake_failed(session, j):
        calls.append("failed")
        j.status = JobStatus.FAILED

    monkeypatch.setattr("app.reconciler.jobs._handle_job_succeeded", fake_succeeded)
    monkeypatch.setattr("app.reconciler.jobs._handle_job_failed", fake_failed)

    monkeypatch.setattr(
        "app.reconciler.jobs.volcano_v1alpha1",
        lambda: type(
            "_FV",
            (),
            {
                "get_namespaced_custom_object": lambda self, **_kw: {
                    "status": {"state": {"phase": "Running"}}
                }
            },
        )(),
    )
    monkeypatch.setattr("app.reconciler.jobs._job_timed_out", lambda j, v: False)

    await reconcile_job(db_session, job)

    assert calls == ["succeeded"], f"expected succeeded path, got {calls}"


@pytest.mark.asyncio
async def test_reconcile_trusts_stage_end_failure(db_session, monkeypatch) -> None:
    from app.reconciler import reconcile_job

    job = await _seed_job_running(db_session)
    db_session.add(
        JobEvent(
            job_id=job.id,
            ts=datetime.now(UTC),
            kind="stage_end",
            payload={"stage": "train", "status": "failure"},
        )
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_succeeded(session, j):
        calls.append("succeeded")

    async def fake_failed(session, j):
        calls.append("failed")

    monkeypatch.setattr("app.reconciler.jobs._handle_job_succeeded", fake_succeeded)
    monkeypatch.setattr("app.reconciler.jobs._handle_job_failed", fake_failed)
    monkeypatch.setattr(
        "app.reconciler.jobs.volcano_v1alpha1",
        lambda: type(
            "_FV",
            (),
            {
                "get_namespaced_custom_object": lambda self, **_kw: {
                    "status": {"state": {"phase": "Running"}}
                }
            },
        )(),
    )
    monkeypatch.setattr("app.reconciler.jobs._job_timed_out", lambda j, v: False)

    await reconcile_job(db_session, job)
    assert calls == ["failed"]


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_volcano_phase_when_no_events(
    db_session, monkeypatch
) -> None:
    from app.reconciler import reconcile_job

    job = await _seed_job_running(db_session)
    # No events seeded

    calls: list[str] = []

    async def fake_succeeded(session, j):
        calls.append("succeeded")

    async def fake_failed(session, j):
        calls.append("failed")

    async def fake_update(session, j):
        calls.append("progress")

    monkeypatch.setattr("app.reconciler.jobs._handle_job_succeeded", fake_succeeded)
    monkeypatch.setattr("app.reconciler.jobs._handle_job_failed", fake_failed)
    monkeypatch.setattr("app.reconciler.jobs._update_job_progress", fake_update)
    monkeypatch.setattr(
        "app.reconciler.jobs.volcano_v1alpha1",
        lambda: type(
            "_FV",
            (),
            {
                "get_namespaced_custom_object": lambda self, **_kw: {
                    "status": {"state": {"phase": "Completed"}}
                }
            },
        )(),
    )
    monkeypatch.setattr("app.reconciler.jobs._job_timed_out", lambda j, v: False)

    await reconcile_job(db_session, job)
    assert calls == ["succeeded"]


@pytest.mark.asyncio
async def test_reconcile_event_wins_against_volcano_failed_race(
    db_session,
    monkeypatch,
) -> None:
    """Race: stage_end=success flushed by sidecar AFTER the detector pod's
    exit was already observed as a failure by Volcano (e.g. sidecar lingered
    a moment past the detector's 0-exit and Volcano reaped the pod as
    Failed because of the TaskCompleted/PodFailed policy-quirks interaction).

    The stage_end event is ground truth — the detector reported success.
    Trusting the Volcano phase would bury a successful run.
    """
    from app.reconciler import reconcile_job

    job = await _seed_job_running(db_session)
    db_session.add(
        JobEvent(
            job_id=job.id,
            ts=datetime.now(UTC),
            kind="stage_end",
            payload={"stage": "train", "status": "success"},
        )
    )
    await db_session.commit()

    calls: list[str] = []

    async def fake_succeeded(session, j):
        calls.append("succeeded")
        j.status = JobStatus.SUCCEEDED

    async def fake_failed(session, j):
        calls.append("failed")
        j.status = JobStatus.FAILED

    monkeypatch.setattr("app.reconciler.jobs._handle_job_succeeded", fake_succeeded)
    monkeypatch.setattr("app.reconciler.jobs._handle_job_failed", fake_failed)
    # Volcano reports Failed, but the event says success — event wins.
    monkeypatch.setattr(
        "app.reconciler.jobs.volcano_v1alpha1",
        lambda: type(
            "_FV",
            (),
            {
                "get_namespaced_custom_object": lambda self, **_kw: {
                    "status": {"state": {"phase": "Failed"}}
                }
            },
        )(),
    )
    monkeypatch.setattr("app.reconciler.jobs._job_timed_out", lambda j, v: False)

    await reconcile_job(db_session, job)
    assert calls == ["succeeded"], (
        f"stage_end=success must beat Volcano phase=Failed, got {calls}"
    )
