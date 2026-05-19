"""Tests for POST /api/v1/detectors/{detector_id}/builds/{build_id}/cancel.

Covers the cancel_build endpoint in app/routers/detectors.py (previously
zero coverage; the schemathesis contract test only verifies the OpenAPI
response shape via fuzzed UUIDs, never reaches the cancellable / k8s
branches). The cancellable-status whitelist guards an operator footgun:
cancelling a build that already finished or already cancelled would
silently overwrite finished_at.
"""

from unittest.mock import MagicMock

import pytest
from app.models.detector import DetectorBuild, DetectorBuildStatus
from sqlalchemy import select

from tests.conftest import test_session_maker


async def _insert_build(
    detector_id: str,
    status: DetectorBuildStatus = DetectorBuildStatus.PENDING,
    k8s_job_name: str | None = None,
) -> str:
    """Insert a DetectorBuild row owned by the given detector and return its id."""
    from uuid import UUID

    from app.models import User

    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == "dev@example.dev"))
        ).scalar_one()
        build = DetectorBuild(
            detector_id=UUID(detector_id),
            git_tag="v0.1.0",
            triggered_by_id=owner.id,
            status=status,
            k8s_job_name=k8s_job_name,
        )
        session.add(build)
        await session.commit()
        await session.refresh(build)
        return str(build.id)


@pytest.mark.asyncio
async def test_cancel_pending_build_transitions_to_cancelled(
    auth_client_developer, seed_detector
):
    """Happy path: a PENDING build can be cancelled by its detector owner."""
    build_id = await _insert_build(seed_detector, DetectorBuildStatus.PENDING)

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None


@pytest.mark.parametrize(
    "starting_status",
    [
        DetectorBuildStatus.CLONING,
        DetectorBuildStatus.VALIDATING,
        DetectorBuildStatus.BUILDING,
        DetectorBuildStatus.SCANNING,
    ],
)
@pytest.mark.asyncio
async def test_cancel_in_progress_statuses(
    auth_client_developer, seed_detector, starting_status
):
    """Every cancellable status in the whitelist transitions to CANCELLED.

    Reading the whitelist from the handler keeps the contract honest — if
    a future refactor narrows the set (e.g. dropping SCANNING because the
    Trivy scan is bookkeeping-only), this test will surface the change
    instead of silently letting a now-rejected status look cancellable.
    """
    build_id = await _insert_build(seed_detector, starting_status)

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "terminal_status",
    [
        DetectorBuildStatus.SUCCEEDED,
        DetectorBuildStatus.FAILED,
        DetectorBuildStatus.TIMEOUT,
        DetectorBuildStatus.CANCELLED,
        DetectorBuildStatus.CVE_BLOCKED,
    ],
)
@pytest.mark.asyncio
async def test_cancel_terminal_build_returns_409(
    auth_client_developer, seed_detector, terminal_status
):
    """Builds in terminal states cannot be cancelled — 409 with the
    `not_cancellable` code so the UI can disable the button without a
    string-match on the message."""
    build_id = await _insert_build(seed_detector, terminal_status)

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "not_cancellable"


@pytest.mark.asyncio
async def test_cancel_unknown_build_id_404(auth_client_developer, seed_detector):
    """A random UUID under a valid detector returns 404, not 500."""
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{bogus}/cancel"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "build not found"


@pytest.mark.asyncio
async def test_cancel_build_belonging_to_different_detector_404(
    auth_client_developer, seed_detector, monkeypatch
):
    """A build whose detector_id mismatches the path detector returns 404.

    Belt-and-braces: even when the build_id resolves to a real row, the
    cross-detector mismatch must surface as not-found (the same code
    used for an entirely missing build), so a caller cannot probe build
    IDs across detectors they can read.
    """
    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {"name": "other-det", "description": "x", "display_name": "other"}

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)
    other = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/other-det"},
    )
    assert other.status_code == 201, other.text
    other_did = other.json()["id"]

    foreign_build_id = await _insert_build(other_did, DetectorBuildStatus.PENDING)

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{foreign_build_id}/cancel"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_build_by_non_owner_403(seed_detector, client):
    """Non-owner non-admin gets 403 (require_detector_access write=True).

    ``seed_detector`` is owned by ``dev@example.dev``; flip the shared
    client's header to a user that doesn't exist as owner. We need a
    seeded non-owner User row, then switch the client's header to point
    at it — the shared `client` fixture mutates a single AsyncClient,
    so requesting `auth_client_user` alongside `auth_client_developer`
    would race the header (last fixture wins, breaks ``seed_detector``).
    """
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("nonowner@example.dev", role=Role.USER)
    build_id = await _insert_build(seed_detector, DetectorBuildStatus.PENDING)

    client.headers["x-test-user-email"] = "nonowner@example.dev"
    resp = await client.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cancel_build_by_admin_succeeds(seed_detector, client):
    """Admin can cancel any detector's build (write-access bypass via Role.ADMIN).

    Same fixture-racing concern as ``test_cancel_build_by_non_owner_403`` —
    we seed the admin row directly and flip the header on the shared
    client so ``seed_detector`` (which depends on ``auth_client_developer``)
    keeps its dev@example.dev session.
    """
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("admin-cancel@example.dev", role=Role.ADMIN)
    build_id = await _insert_build(seed_detector, DetectorBuildStatus.PENDING)

    client.headers["x-test-user-email"] = "admin-cancel@example.dev"
    resp = await client.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_build_deletes_k8s_job(
    auth_client_developer, seed_detector, monkeypatch
):
    """If the build has a k8s_job_name, the handler calls
    batch_v1().delete_namespaced_job with Background propagation."""
    from app.routers import detectors as dr

    delete_calls: list[dict] = []

    def fake_delete(*, name, namespace, propagation_policy):
        delete_calls.append(
            {"name": name, "namespace": namespace, "policy": propagation_policy}
        )

    fake_batch = MagicMock()
    fake_batch.delete_namespaced_job.side_effect = fake_delete
    monkeypatch.setattr(dr, "batch_v1", lambda: fake_batch)

    build_id = await _insert_build(
        seed_detector,
        DetectorBuildStatus.BUILDING,
        k8s_job_name="lolday-build-xyz",
    )

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert len(delete_calls) == 1
    assert delete_calls[0]["name"] == "lolday-build-xyz"
    assert delete_calls[0]["policy"] == "Background"


@pytest.mark.asyncio
async def test_cancel_build_swallows_k8s_failure(
    auth_client_developer, seed_detector, monkeypatch
):
    """K8s deletion is best-effort. A failure must not flip the response
    to 5xx — the build still cancels at the DB level and BACKEND_ERRORS
    gets the `cancel_build_k8s_cleanup` stage label so the failure is
    visible on the alert dashboard."""
    from app.metrics import BACKEND_ERRORS
    from app.routers import detectors as dr

    fake_batch = MagicMock()
    fake_batch.delete_namespaced_job.side_effect = RuntimeError("k8s down")
    monkeypatch.setattr(dr, "batch_v1", lambda: fake_batch)

    before = BACKEND_ERRORS.labels(stage="cancel_build_k8s_cleanup")._value.get()

    build_id = await _insert_build(
        seed_detector,
        DetectorBuildStatus.BUILDING,
        k8s_job_name="lolday-build-doomed",
    )

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    after = BACKEND_ERRORS.labels(stage="cancel_build_k8s_cleanup")._value.get()
    assert after == before + 1, (
        "K8s cleanup failure must increment BACKEND_ERRORS{stage=cancel_build_k8s_cleanup}"
    )


@pytest.mark.asyncio
async def test_cancel_build_without_k8s_job_name_does_not_call_batch(
    auth_client_developer, seed_detector, monkeypatch
):
    """A build that never reached the k8s-creation step (k8s_job_name=NULL)
    must not call batch_v1 — the BUILDING / CLONING / etc. path can be
    reached at the application layer for builds that race past the K8s
    submission failure path."""
    from app.routers import detectors as dr

    fake_batch = MagicMock()
    monkeypatch.setattr(dr, "batch_v1", lambda: fake_batch)

    build_id = await _insert_build(
        seed_detector, DetectorBuildStatus.PENDING, k8s_job_name=None
    )

    resp = await auth_client_developer.post(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}/cancel"
    )
    assert resp.status_code == 200
    fake_batch.delete_namespaced_job.assert_not_called()
