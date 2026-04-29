from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_create_build_triggers_k8s_job(
    auth_client_developer, seed_detector, monkeypatch
):
    from app.routers import detectors as dr

    monkeypatch.setattr(
        dr, "_create_k8s_resources", AsyncMock(return_value="build-xxx-123")
    )

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds",
        json={"git_tag": "v0.1.0"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["git_tag"] == "v0.1.0"
    assert (
        body["status"] == "cloning"
    )  # after successful k8s resource creation it transitions to cloning


@pytest.mark.asyncio
async def test_duplicate_in_flight_build_returns_409(
    auth_client_developer, seed_detector, monkeypatch
):
    from app.routers import detectors as dr

    monkeypatch.setattr(
        dr, "_create_k8s_resources", AsyncMock(return_value="build-xxx-123")
    )

    r1 = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    assert r1.status_code == 201
    r2 = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    assert r2.status_code == 409
    assert r2.json()["detail"].get("code") == "build_in_flight"


@pytest.mark.asyncio
async def test_per_user_concurrency_cap(
    auth_client_developer, seed_detector, monkeypatch
):
    from app.routers import detectors as dr

    monkeypatch.setattr(
        dr, "_create_k8s_resources", AsyncMock(return_value="build-xxx-123")
    )

    # Override settings concurrency cap for this test
    from app.config import settings

    original = settings.BUILD_CONCURRENCY_PER_USER
    settings.BUILD_CONCURRENCY_PER_USER = 1
    try:
        r1 = await auth_client_developer.post(
            f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
        )
        assert r1.status_code == 201
        r2 = await auth_client_developer.post(
            f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.2.0"}
        )
        assert r2.status_code == 429
        detail = r2.json()["detail"]
        # 429 detail must state the limit + current in-flight count so the
        # caller doesn't need out-of-band knowledge of the platform config.
        assert detail["code"] == "concurrency_limit"
        assert detail["limit"] == 1
        assert detail["in_flight"] == 1
    finally:
        settings.BUILD_CONCURRENCY_PER_USER = original


@pytest.mark.asyncio
async def test_build_creation_returns_500_and_marks_failed_on_k8s_error(
    auth_client_developer, seed_detector, monkeypatch
):
    """If _create_k8s_resources raises, endpoint returns 500 (not 201) and the
    build row is persisted as FAILED with failure_reason. Clients know the
    launch failed at request time, not by polling build status.

    Secret rollback itself happens inside _create_k8s_resources (unit-test
    deferred — requires real K8s client mock). Here we verify the API
    contract at the endpoint level.
    """
    from app.routers import detectors as dr

    async def failing_create(*args, **kwargs):
        raise RuntimeError("simulated k8s job creation failure")

    monkeypatch.setattr(dr, "_create_k8s_resources", failing_create)

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds",
        json={"git_tag": "v0.1.0"},
    )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["code"] == "build_launch_failed"
    assert "build_id" in detail

    # Verify the DB build row is persisted as FAILED with failure_reason set
    from app.models.detector import DetectorBuild, DetectorBuildStatus
    from sqlalchemy import select

    from tests.conftest import test_session_maker

    async with test_session_maker() as session:
        res = await session.execute(
            select(DetectorBuild).where(DetectorBuild.git_tag == "v0.1.0")
        )
        build = res.scalar_one()
        assert build.status == DetectorBuildStatus.FAILED
        assert "simulated k8s job creation failure" in (build.failure_reason or "")


@pytest.mark.asyncio
async def test_flat_builds_get_resolves_detector(
    auth_client_developer, seed_detector, monkeypatch
):
    """GET /api/v1/builds/<id> returns the same shape as the nested route.
    Phase 8 finding: polling scripts naturally reach for this path first.
    """
    from app.routers import detectors as dr

    monkeypatch.setattr(dr, "_create_k8s_resources", AsyncMock(return_value="b-x"))
    create = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds", json={"git_tag": "v0.1.0"}
    )
    assert create.status_code == 201
    build_id = create.json()["id"]

    flat = await auth_client_developer.get(f"/api/v1/builds/{build_id}")
    assert flat.status_code == 200, flat.text
    assert flat.json()["id"] == build_id
    assert flat.json()["git_tag"] == "v0.1.0"


@pytest.mark.asyncio
async def test_flat_builds_get_unknown_id_404(auth_client_developer):
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await auth_client_developer.get(f"/api/v1/builds/{bogus}")
    assert resp.status_code == 404
