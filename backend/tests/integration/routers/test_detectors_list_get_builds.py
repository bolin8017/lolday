"""Tests for the read-side build endpoints on detectors.py.

Covers:
- GET /api/v1/detectors/{detector_id}/builds       (list_builds)
- GET /api/v1/detectors/{detector_id}/builds/{id}  (get_build, NESTED route)

Both endpoints had zero functional coverage prior to this PR. The
existing test_builds.py only exercises the FLAT alias route
``/api/v1/builds/{id}`` and the POST create path. The schemathesis
contract test under tests/contract/openapi/test_schemathesis_detectors.py
only fuzzes random UUIDs and never hits the limit / offset / ordering
branches.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.models.detector import DetectorBuild, DetectorBuildStatus
from sqlalchemy import select

from tests.conftest import test_session_maker


async def _insert_build(
    detector_id: str,
    *,
    git_tag: str = "v0.1.0",
    status: DetectorBuildStatus = DetectorBuildStatus.SUCCEEDED,
    started_at: datetime | None = None,
) -> str:
    """Insert a DetectorBuild row with deterministic owner + optional
    started_at offset. Returns the new id as a string UUID."""
    from app.models import User

    async with test_session_maker() as session:
        owner = (
            await session.execute(select(User).where(User.email == "dev@example.dev"))
        ).scalar_one()
        build = DetectorBuild(
            detector_id=UUID(detector_id),
            git_tag=git_tag,
            triggered_by_id=owner.id,
            status=status,
        )
        if started_at is not None:
            build.started_at = started_at
        session.add(build)
        await session.commit()
        await session.refresh(build)
        return str(build.id)


# ---------------------------------------------------------------------------
# GET /api/v1/detectors/{detector_id}/builds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_builds_returns_empty_for_new_detector(
    auth_client_developer, seed_detector
):
    """A freshly-seeded detector with no builds returns an empty `items`
    list and echoes the request's limit / offset back to the caller."""
    resp = await auth_client_developer.get(f"/api/v1/detectors/{seed_detector}/builds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    # Defaults from the handler signature.
    assert body["limit"] == 20
    assert body["offset"] == 0


@pytest.mark.asyncio
async def test_list_builds_orders_by_started_at_desc(
    auth_client_developer, seed_detector
):
    """The handler orders by `started_at` DESC so the most recent
    build is first. Tested with three rows whose `started_at` is
    explicitly set to enforce ordering independent of insert order."""
    now = datetime.now(UTC)
    middle_id = await _insert_build(
        seed_detector, git_tag="v0.2.0", started_at=now - timedelta(hours=1)
    )
    newest_id = await _insert_build(seed_detector, git_tag="v0.3.0", started_at=now)
    oldest_id = await _insert_build(
        seed_detector, git_tag="v0.1.0", started_at=now - timedelta(days=1)
    )

    resp = await auth_client_developer.get(f"/api/v1/detectors/{seed_detector}/builds")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3
    assert [b["id"] for b in items] == [newest_id, middle_id, oldest_id]


@pytest.mark.asyncio
async def test_list_builds_applies_limit_and_offset(
    auth_client_developer, seed_detector
):
    """`limit=1&offset=1` returns the SECOND-most-recent build only.

    The handler also echoes the input limit/offset back in the
    response so the UI doesn't have to remember pagination state.
    """
    now = datetime.now(UTC)
    await _insert_build(seed_detector, git_tag="b1", started_at=now)
    second_id = await _insert_build(
        seed_detector, git_tag="b2", started_at=now - timedelta(seconds=10)
    )
    await _insert_build(
        seed_detector, git_tag="b3", started_at=now - timedelta(seconds=20)
    )

    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds?limit=1&offset=1"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == second_id
    assert body["limit"] == 1
    assert body["offset"] == 1


@pytest.mark.asyncio
async def test_list_builds_rejects_limit_over_cap(auth_client_developer, seed_detector):
    """`Query(le=100)` caps limit at 100 — anything bigger is a 422.

    Without this cap a caller could pull thousands of builds in one
    request and DOS the JSON serializer.
    """
    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds?limit=101"
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_builds_scopes_to_one_detector(
    auth_client_developer, seed_detector, monkeypatch
):
    """A build belonging to detector B does NOT appear in detector A's
    list response — guards against an over-broad WHERE clause."""
    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {
            "name": "other-list",
            "description": "x",
            "display_name": "other-list",
        }

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)
    other = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/other-list"},
    )
    assert other.status_code == 201, other.text
    other_detector_id = other.json()["id"]

    # Build under detector A
    own_build_id = await _insert_build(seed_detector, git_tag="own-v1")
    # Build under detector B
    await _insert_build(other_detector_id, git_tag="other-v1")

    resp = await auth_client_developer.get(f"/api/v1/detectors/{seed_detector}/builds")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == own_build_id


@pytest.mark.asyncio
async def test_list_builds_readable_by_any_authenticated_user(seed_detector, client):
    """`require_detector_access(write=False)` lets any authenticated
    user list — owner check only applies to write operations. Verified
    by flipping the client header to a non-owner USER and confirming
    the read still returns 200.
    """
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("reader-list@example.dev", role=Role.USER)
    await _insert_build(seed_detector, git_tag="v0.read.1")

    client.headers["x-test-user-email"] = "reader-list@example.dev"
    resp = await client.get(f"/api/v1/detectors/{seed_detector}/builds")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


# ---------------------------------------------------------------------------
# GET /api/v1/detectors/{detector_id}/builds/{build_id}  (nested route)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_build_returns_build_when_found(auth_client_developer, seed_detector):
    """Happy path: the nested route returns a single BuildRead for
    a build that belongs to the path detector."""
    build_id = await _insert_build(seed_detector, git_tag="v1.0.0")

    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds/{build_id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == build_id
    assert body["git_tag"] == "v1.0.0"


@pytest.mark.asyncio
async def test_get_build_unknown_id_404(auth_client_developer, seed_detector):
    """Unknown UUID under a valid detector returns 404 — not 500."""
    bogus = "00000000-0000-0000-0000-000000000000"
    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds/{bogus}"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "build not found"


@pytest.mark.asyncio
async def test_get_build_cross_detector_id_404(
    auth_client_developer, seed_detector, monkeypatch
):
    """If the build_id resolves but belongs to detector B, requesting
    it under detector A returns 404 (not 200). Without the
    `build.detector_id != detector.id` guard, a caller could probe
    build IDs across detectors they don't read.
    """
    from app.routers import detectors as dr

    async def fake_meta(url, pat):
        return {"name": "other-get", "description": "x", "display_name": "other-get"}

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)
    other = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/bolin8017/other-get"},
    )
    assert other.status_code == 201
    foreign_build_id = await _insert_build(other.json()["id"], git_tag="other-v1")

    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds/{foreign_build_id}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_build_readable_by_any_authenticated_user(seed_detector, client):
    """Same `write=False` access rule as list_builds — a non-owner USER
    can read a build that belongs to the seed detector.
    """
    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("reader-get@example.dev", role=Role.USER)
    build_id = await _insert_build(seed_detector, git_tag="v.shared.1")

    client.headers["x-test-user-email"] = "reader-get@example.dev"
    resp = await client.get(f"/api/v1/detectors/{seed_detector}/builds/{build_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == build_id


@pytest.mark.asyncio
async def test_get_build_rejects_invalid_uuid_with_422(
    auth_client_developer, seed_detector
):
    """The path validator catches a non-UUID build_id at the framework
    layer and returns 422 — never reaches the handler body. Confirms
    the UUID coercion is wired correctly (regression guard for a
    `str` typo in the signature)."""
    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{seed_detector}/builds/not-a-uuid"
    )
    assert resp.status_code == 422


# Note: `available_tags` and the `_create_k8s_resources` helper remain
# uncovered. Those touch real external surfaces (GitHub PAT, K8s job
# create) and need a separate spec-lane PR to mock the GitHub tag API
# without leaking the user's PAT. Out of scope here.
