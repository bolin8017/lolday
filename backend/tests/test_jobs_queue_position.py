"""Tests for GET /api/v1/jobs/{id}/queue-position."""

from unittest.mock import patch

import pytest


async def _create_job(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 1},
        },
    )
    assert r.status_code == 202, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_queue_position_requires_auth(client):
    r = await client.get(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000/queue-position"
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_queue_position_returns_null_for_queued_backend_job(
    user_client, seed_detector_version, seed_dataset
):
    """Phase 6 (Task E): POST creates jobs with status=queued_backend and no
    k8s_job_name yet. The queue-position endpoint returns null for such jobs
    because they have not been submitted to Volcano by the reconciler yet."""
    jid = await _create_job(user_client, seed_detector_version, seed_dataset)
    r = await user_client.get(f"/api/v1/jobs/{jid}/queue-position")
    assert r.status_code == 200
    assert r.json() == {"position": None}


@pytest.mark.asyncio
async def test_queue_position_returns_number_when_k8s_job_name_is_set(
    user_client, seed_detector_version, seed_dataset, db_session
):
    """Once the reconciler dispatches the job and sets k8s_job_name, the
    endpoint delegates to get_job_queue_position. Simulate by directly
    setting k8s_job_name on the DB row."""
    from uuid import UUID

    from app.models.job import Job
    from sqlalchemy import select

    jid = await _create_job(user_client, seed_detector_version, seed_dataset)
    # Simulate reconciler having set k8s_job_name after Volcano dispatch
    job = (
        await db_session.execute(select(Job).where(Job.id == UUID(jid)))
    ).scalar_one()
    job.k8s_job_name = "vcjob-test-abc123"
    await db_session.commit()

    with patch("app.routers.jobs.get_job_queue_position", return_value=2):
        r = await user_client.get(f"/api/v1/jobs/{jid}/queue-position")
    assert r.status_code == 200
    assert r.json() == {"position": 2}


@pytest.mark.asyncio
async def test_queue_position_returns_null_when_not_queued(
    user_client, seed_detector_version, seed_dataset
):
    jid = await _create_job(user_client, seed_detector_version, seed_dataset)
    with patch("app.routers.jobs.get_job_queue_position", return_value=None):
        r = await user_client.get(f"/api/v1/jobs/{jid}/queue-position")
    assert r.status_code == 200
    assert r.json() == {"position": None}


@pytest.mark.asyncio
async def test_queue_position_404_when_job_missing(user_client):
    r = await user_client.get(
        "/api/v1/jobs/00000000-0000-0000-0000-000000000000/queue-position"
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_queue_position_forbidden_for_other_user(
    user_client, second_user_client, seed_detector_version, seed_dataset
):
    jid = await _create_job(user_client, seed_detector_version, seed_dataset)
    r = await second_user_client.get(f"/api/v1/jobs/{jid}/queue-position")
    assert r.status_code in (403, 404)  # hide-or-forbid both acceptable
