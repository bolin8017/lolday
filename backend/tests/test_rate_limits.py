"""Rate limit enforcement on job create / build create.

Phase 10.2 removed the login rate-limit tests along with the login
endpoints they exercised — Cloudflare Access rate-limits auth attempts
at the edge, so an app-level bucket is redundant.

Remaining limits (per design):
- POST /api/v1/jobs                       : 30/min  (per authenticated user)
- POST /api/v1/detectors/{id}/builds      : 10/hour (per authenticated user)
"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_job_create_over_limit_returns_429(
    user_client, seed_detector_version, seed_dataset, monkeypatch
):
    from app.config import settings
    monkeypatch.setattr(settings, "JOB_PER_USER_CONCURRENCY", 100)  # raise so rate limit trips first

    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")

    saw_429 = False
    for i in range(31):
        r = await user_client.post(
            "/api/v1/jobs",
            json={
                "type": "train",
                "detector_version_id": dv_id,
                "train_dataset_id": tr,
                "test_dataset_id": te,
                "params": {"seed": i},
            },
        )
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "expected a 429 within 31 job-create attempts"


@pytest.mark.asyncio
async def test_build_create_over_limit_returns_429(
    auth_client_developer, seed_detector, monkeypatch
):
    from app.routers import detectors as dr
    monkeypatch.setattr(dr, "_create_k8s_resources", AsyncMock(return_value="build-xxx"))

    # Raise per-user concurrency + bypass in-flight duplicate check to let
    # rate limit be the bottleneck. Use different git tags each iter so the
    # in-flight duplicate check doesn't pre-empt.
    from app.config import settings
    monkeypatch.setattr(settings, "BUILD_CONCURRENCY_PER_USER", 100)

    saw_429 = False
    for i in range(11):
        r = await auth_client_developer.post(
            f"/api/v1/detectors/{seed_detector}/builds",
            json={"git_tag": f"v0.{i}.0"},
        )
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "expected 429 within 11 build attempts"


@pytest.mark.asyncio
async def test_job_rate_limit_independent_per_user(
    user_client, second_user_client, seed_detector_version, seed_dataset, monkeypatch
):
    from app.config import settings
    monkeypatch.setattr(settings, "JOB_PER_USER_CONCURRENCY", 100)

    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")

    # user1 exhausts limit
    for i in range(30):
        await user_client.post(
            "/api/v1/jobs",
            json={
                "type": "train",
                "detector_version_id": dv_id,
                "train_dataset_id": tr,
                "test_dataset_id": te,
                "params": {"seed": 1000 + i},
            },
        )
    # 31st must be 429
    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 9999},
        },
    )
    assert r.status_code == 429

    # user2 (fresh) — any status except 429 means the limit is per-user not per-server
    r2 = await second_user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 5555},
        },
    )
    assert r2.status_code != 429, f"user2 should not inherit user1's rate quota (got {r2.status_code})"
