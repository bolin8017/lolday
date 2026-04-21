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
    r = await client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000/queue-position")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_queue_position_returns_number_for_pending_job(
    user_client, seed_detector_version, seed_dataset
):
    jid = await _create_job(user_client, seed_detector_version, seed_dataset)
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
