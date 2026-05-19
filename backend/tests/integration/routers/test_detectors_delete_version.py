"""Tests for DELETE /api/v1/detectors/{detector_id}/versions/{tag}.

Covers the `delete_version` endpoint in `app/routers/detectors.py`
(lines 442-520). The endpoint had zero functional coverage; the only
existing reference is the schemathesis contract test which fuzzes
random UUIDs and never reaches the status / in-flight / Harbor
branches. Soft-delete semantics: the row stays for historical-job
read paths, only `status` flips to `DELETED`.
"""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.models import Detector, DetectorVersion, Job, Role, User
from app.models.detector import DetectorVersionStatus
from app.models.job import JobStatus, JobType
from sqlalchemy import select

from tests.conftest import _make_user, test_session_maker


async def _seed_detector_with_version(
    owner_email: str,
    *,
    name_suffix: str | None = None,
    version_status: DetectorVersionStatus = DetectorVersionStatus.ACTIVE,
    git_tag: str = "v0.1.0",
) -> tuple[str, str]:
    """Insert a Detector + DetectorVersion row owned by ``owner_email``.

    Returns ``(detector_id, version_id)`` as string UUIDs. The detector
    name carries a unique suffix so tests don't collide on the
    ``detector.name`` unique constraint when the fixture is invoked
    twice in the same session.
    """
    suffix = name_suffix or uuid4().hex[:8]
    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == owner_email))
        ).scalar_one()
        detector = Detector(
            name=f"det-{suffix}",
            display_name=f"det-{suffix}",
            git_url=f"https://github.com/test/det-{suffix}.git",
            owner_id=owner.id,
        )
        session.add(detector)
        await session.flush()
        version = DetectorVersion(
            detector_id=detector.id,
            git_tag=git_tag,
            git_sha="a" * 40,
            harbor_image=f"harbor.harbor.svc:80/detectors/det-{suffix}:{git_tag}",
            image_digest="sha256:" + "0" * 64,
            status=version_status,
        )
        session.add(version)
        await session.commit()
        await session.refresh(version)
        return str(detector.id), str(version.id)


@pytest.mark.asyncio
async def test_delete_version_happy_path_returns_204_and_soft_deletes(
    auth_client_developer,
):
    """Owner deletes ACTIVE version: 204, row stays, status flips to DELETED."""
    detector_id, version_id = await _seed_detector_with_version(
        "dev@example.dev",
    )

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 204
    assert resp.content == b""

    async with test_session_maker() as session:
        row = await session.get(DetectorVersion, UUID(version_id))
        assert row is not None, "row must persist (soft-delete only)"
        assert row.status == DetectorVersionStatus.DELETED


@pytest.mark.asyncio
async def test_delete_version_unknown_tag_returns_404(auth_client_developer):
    """Wrong tag under a valid detector returns 404."""
    detector_id, _ = await _seed_detector_with_version("dev@example.dev")

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v9.9.9"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "version not found"


@pytest.mark.parametrize(
    "starting_status",
    [
        DetectorVersionStatus.DELETED,
        DetectorVersionStatus.RETENTION_PRUNED,
    ],
)
@pytest.mark.asyncio
async def test_delete_version_non_active_returns_409(
    auth_client_developer, starting_status
):
    """Already-deleted (or retention-pruned) versions can't be re-deleted.

    Reason for the explicit 409: ACTIVE is the only state where the Harbor
    artifact may still be live. Letting a DELETED row run through the
    handler a second time would re-issue the Harbor purge (wasted call)
    and obscure that the operation is a no-op. The error code
    `version_not_active` lets the UI render the actual state, not
    "deletion failed".
    """
    detector_id, _ = await _seed_detector_with_version(
        "dev@example.dev", version_status=starting_status
    )

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "version_not_active"


@pytest.mark.asyncio
async def test_delete_version_with_in_flight_job_returns_409(auth_client_developer):
    """A version with a non-terminal Job referencing it cannot be deleted.

    Tested with one of the `NON_TERMINAL_STATUSES` (RUNNING) to confirm the
    `.in_(NON_TERMINAL_STATUSES)` membership check is wired correctly.
    Operators get an actionable error: cancel-the-job, not retry-delete.
    """
    detector_id, version_id = await _seed_detector_with_version("dev@example.dev")

    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == "dev@example.dev"))
        ).scalar_one()
        in_flight_job = Job(
            type=JobType.TRAIN,
            status=JobStatus.RUNNING,
            detector_version_id=UUID(version_id),
            owner_id=owner.id,
            resolved_config={},
            mlflow_run_id=uuid4().hex,
            idempotency_key=uuid4().hex,
        )
        session.add(in_flight_job)
        await session.commit()

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "version_has_in_flight_jobs"


@pytest.mark.asyncio
async def test_delete_version_with_terminal_job_succeeds(auth_client_developer):
    """A job in a terminal state (SUCCEEDED) doesn't block delete — only
    non-terminal jobs do. This guards against a regression that would
    treat completed historical jobs as still active.
    """
    detector_id, version_id = await _seed_detector_with_version("dev@example.dev")

    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == "dev@example.dev"))
        ).scalar_one()
        terminal_job = Job(
            type=JobType.TRAIN,
            status=JobStatus.SUCCEEDED,
            detector_version_id=UUID(version_id),
            owner_id=owner.id,
            resolved_config={},
            mlflow_run_id=uuid4().hex,
            idempotency_key=uuid4().hex,
        )
        session.add(terminal_job)
        await session.commit()

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_version_non_owner_403(seed_detector, client):
    """`require_detector_access(write=True)` rejects non-owner non-admin.

    Header-swap pattern: the shared client fixture is a single
    AsyncClient. Requesting both `auth_client_user` and
    `auth_client_developer` would race the `x-test-user-email` header
    and break ``seed_detector`` (precedent: see
    ``test_detectors_cancel_build.py``).
    """
    await _make_user("nonowner-del@example.dev", role=Role.USER)

    # Insert an ACTIVE version on the seeded detector.
    async with test_session_maker() as session:
        version = DetectorVersion(
            detector_id=UUID(seed_detector),
            git_tag="v0.2.0",
            git_sha="b" * 40,
            harbor_image="harbor/x:v0.2.0",
            image_digest="sha256:" + "1" * 64,
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(version)
        await session.commit()

    client.headers["x-test-user-email"] = "nonowner-del@example.dev"
    resp = await client.delete(f"/api/v1/detectors/{seed_detector}/versions/v0.2.0")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_version_admin_succeeds(seed_detector, client):
    """Admin can delete any detector's version (Role.ADMIN write bypass)."""
    await _make_user("admin-delver@example.dev", role=Role.ADMIN)

    async with test_session_maker() as session:
        version = DetectorVersion(
            detector_id=UUID(seed_detector),
            git_tag="v0.3.0",
            git_sha="c" * 40,
            harbor_image="harbor/x:v0.3.0",
            image_digest="sha256:" + "2" * 64,
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(version)
        await session.commit()

    client.headers["x-test-user-email"] = "admin-delver@example.dev"
    resp = await client.delete(f"/api/v1/detectors/{seed_detector}/versions/v0.3.0")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_version_runs_harbor_purge_when_password_set(
    auth_client_developer, monkeypatch
):
    """When HARBOR_ADMIN_PASSWORD is non-empty, the handler instantiates
    HarborClient and calls delete_tag_or_artifact with the project /
    detector name / tag / digest tuple. Without the password it skips."""
    from app.config import settings
    from app.routers import detectors as dr

    delete_calls: list[tuple] = []

    class FakeHarbor:
        def __init__(self, *args, **kwargs):
            self.args = args

        async def delete_tag_or_artifact(self, project, name, tag, digest):
            delete_calls.append((project, name, tag, digest))

    monkeypatch.setattr(dr, "HarborClient", FakeHarbor)
    monkeypatch.setattr(settings, "HARBOR_ADMIN_PASSWORD", "test-harbor-pw")

    detector_id, _ = await _seed_detector_with_version(
        "dev@example.dev", name_suffix="harbor-purge"
    )

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 204
    assert len(delete_calls) == 1
    project, name, tag, digest = delete_calls[0]
    assert project == "detectors"
    assert name == "det-harbor-purge"
    assert tag == "v0.1.0"
    assert digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_delete_version_skips_harbor_when_password_empty(
    auth_client_developer, monkeypatch
):
    """When HARBOR_ADMIN_PASSWORD is empty (dev box, ephemeral test env),
    the Harbor purge step is skipped silently — soft-delete still
    succeeds. Avoids exploding a happy-path test on a missing operator
    secret."""
    from app.config import settings
    from app.routers import detectors as dr

    fake_harbor_ctor = AsyncMock()
    monkeypatch.setattr(dr, "HarborClient", fake_harbor_ctor)
    monkeypatch.setattr(settings, "HARBOR_ADMIN_PASSWORD", "")

    detector_id, _ = await _seed_detector_with_version(
        "dev@example.dev", name_suffix="no-harbor"
    )

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 204
    fake_harbor_ctor.assert_not_called()


@pytest.mark.asyncio
async def test_delete_version_swallows_harbor_failure(
    auth_client_developer, monkeypatch
):
    """Harbor purge is best-effort; a failure must not flip the response
    to 5xx — soft-delete is the source of truth, Harbor cleanup is a
    follow-up that the reconciler retries on its own schedule.
    `BACKEND_ERRORS{stage="version_delete_harbor"}` increments so the
    failure is visible on dashboards."""
    from app.config import settings
    from app.metrics import BACKEND_ERRORS
    from app.routers import detectors as dr

    class ExplodingHarbor:
        def __init__(self, *args, **kwargs):
            pass

        async def delete_tag_or_artifact(self, *args, **kwargs):
            raise RuntimeError("simulated harbor 502")

    monkeypatch.setattr(dr, "HarborClient", ExplodingHarbor)
    monkeypatch.setattr(settings, "HARBOR_ADMIN_PASSWORD", "test-harbor-pw")

    before = BACKEND_ERRORS.labels(stage="version_delete_harbor")._value.get()

    detector_id, _ = await _seed_detector_with_version(
        "dev@example.dev", name_suffix="harbor-bust"
    )

    resp = await auth_client_developer.delete(
        f"/api/v1/detectors/{detector_id}/versions/v0.1.0"
    )
    assert resp.status_code == 204

    after = BACKEND_ERRORS.labels(stage="version_delete_harbor")._value.get()
    assert after == before + 1
