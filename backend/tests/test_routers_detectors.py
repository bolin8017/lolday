from uuid import UUID

import pytest


@pytest.mark.asyncio
async def test_get_version_legacy_null_manifest_returns_200(
    auth_client_developer, db_session, monkeypatch
):
    """Phase 13a A1: legacy versions built before maldet 1.1 have manifest=NULL.

    Schema must accept None; endpoint must return 200 with `manifest: null`.
    """
    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus
    from app.routers import detectors as dr

    # Fake the _clone_and_validate so detector registration works
    async def fake_meta(url, pat):
        return {
            "name": "legacy-det",
            "description": "demo",
            "display_name": "legacy-det",
        }

    monkeypatch.setattr(dr, "_clone_and_validate", fake_meta)

    # Create detector via API
    create_resp = await auth_client_developer.post(
        "/api/v1/detectors",
        json={"git_url": "https://github.com/test/legacy-det.git"},
    )
    assert create_resp.status_code == 201
    detector_id_str = create_resp.json()["id"]
    detector_id = UUID(detector_id_str)

    # Create legacy version with manifest=None directly in DB
    detector = await db_session.get(Detector, detector_id)
    legacy_version = DetectorVersion(
        detector_id=detector.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image="harbor.harbor.svc:80/detectors/legacy-det:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=None,  # ← legacy build, NULL in DB
    )
    db_session.add(legacy_version)
    await db_session.commit()

    # GET /api/v1/detectors/{id}/versions/{tag} should return 200 with manifest: null
    resp = await auth_client_developer.get(
        f"/api/v1/detectors/{detector_id_str}/versions/v0.1.0",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["manifest"] is None
    assert body["git_tag"] == "v0.1.0"


# ---------------------------------------------------------------------------
# Phase 13a A4 — DELETE /detectors/{id}/versions/{tag}
# ---------------------------------------------------------------------------


class FakeHarborWithTags:
    """Fake HarborClient that tracks tag-level state.

    `tags` maps digest → list of tag names currently on that digest.
    `calls` records (method_name, project, repo, *args) tuples for assertions.
    """

    def __init__(self, *args, tags: dict[str, list[str]] | None = None, **kwargs):
        self.tags: dict[str, list[str]] = dict(tags or {})
        self.calls: list[tuple] = []

    async def delete_tag_or_artifact(
        self, project: str, repo: str, tag: str, digest: str
    ) -> None:
        self.calls.append(("delete_tag_or_artifact", project, repo, tag, digest))
        current = self.tags.get(digest, [])
        if tag not in current:
            return
        if len(current) > 1:
            current.remove(tag)
            self.tags[digest] = current
        else:
            self.tags.pop(digest, None)


@pytest.mark.asyncio
async def test_delete_version_soft_deletes(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """Happy path: soft-deletes the version and best-effort purges Harbor."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        image_digest="sha256:abc",
    )

    fake = FakeHarborWithTags(tags={"sha256:abc": ["v1.0.0"]})
    monkeypatch.setattr("app.routers.detectors.HarborClient", lambda *a, **k: fake)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    list_resp = await async_client.get(
        f"/api/v1/detectors/{detector.id}/versions",
        headers=auth_owner_headers,
    )
    assert all(v["git_tag"] != "v1.0.0" for v in list_resp.json()["items"])
    assert fake.calls == [
        ("delete_tag_or_artifact", "detectors", "rfdet", "v1.0.0", "sha256:abc")
    ]
    # Last tag → digest-level delete → tags map empty
    assert "sha256:abc" not in fake.tags


@pytest.mark.asyncio
async def test_delete_version_blocks_when_in_flight(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
):
    """409 when any job using this version is non-terminal."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    await job_factory(detector_version_id=version.id, status="running")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "version_has_in_flight_jobs"


@pytest.mark.asyncio
async def test_delete_version_404_unknown_tag(
    async_client,
    detector_factory,
    auth_owner_headers,
):
    detector = await detector_factory(name="rfdet")
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/nonexistent",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_version_409_already_deleted(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
):
    detector = await detector_factory(name="rfdet")
    await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        status="deleted",
    )
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "version_not_active"


@pytest.mark.asyncio
async def test_delete_version_403_non_owner(
    async_client,
    detector_factory,
    version_factory,
    auth_other_user_headers,
):
    detector = await detector_factory(name="rfdet")  # owned by `owner`
    await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_other_user_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_version_does_not_break_historical_jobs(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
    monkeypatch,
):
    """After delete, GET /jobs/{historical_job_id} still succeeds and
    references the deleted version row."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    job = await job_factory(detector_version_id=version.id, status="succeeded")

    # No-op Harbor for this test
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/v1.0.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    job_resp = await async_client.get(
        f"/api/v1/jobs/{job.id}",
        headers=auth_owner_headers,
    )
    assert job_resp.status_code == 200
    assert job_resp.json()["detector_version_id"] == str(version.id)


@pytest.mark.asyncio
async def test_delete_detector_blocks_when_in_flight(
    async_client,
    detector_factory,
    version_factory,
    job_factory,
    auth_owner_headers,
):
    """Existing DELETE /detectors/{id} now blocks if any of its versions
    has a non-terminal job. Phase 13a A4."""
    detector = await detector_factory(name="rfdet")
    version = await version_factory(detector_id=detector.id, git_tag="v1.0.0")
    await job_factory(detector_version_id=version.id, status="running")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "detector_has_in_flight_jobs"


@pytest.mark.asyncio
async def test_delete_version_returns_204_when_harbor_purge_fails(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """If Harbor.delete_tag_or_artifact raises, the soft-delete commit must already
    have happened; the endpoint still returns 204 and the row stays DELETED.
    """
    detector = await detector_factory(name="rfdet")
    version = await version_factory(
        detector_id=detector.id,
        git_tag="v1.0.0",
        image_digest="sha256:abc",
    )

    class FakeHarborRaising:
        def __init__(self, *a, **kw):
            pass

        async def delete_tag_or_artifact(self, project, repo, tag, digest):
            raise RuntimeError("harbor down (simulated)")

    monkeypatch.setattr("app.routers.detectors.HarborClient", FakeHarborRaising)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/{version.git_tag}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_version_only_unpins_target_tag_when_digest_shared(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    db_session,
    monkeypatch,
):
    """Two versions share image_digest. DELETE one tag → other tag survives.

    Regression for 2026-05-08 footgun: digest-level delete used to GC the
    shared manifest, leaving the surviving DB row pointing at Harbor 404.
    """
    from app.models.detector import DetectorVersionStatus

    detector = await detector_factory(name="rfdet")
    await version_factory(
        detector_id=detector.id, git_tag="4.1.0", image_digest="sha256:abc"
    )
    v_new = await version_factory(
        detector_id=detector.id, git_tag="v4.1.0", image_digest="sha256:abc"
    )

    fake = FakeHarborWithTags(tags={"sha256:abc": ["4.1.0", "v4.1.0"]})
    monkeypatch.setattr("app.routers.detectors.HarborClient", lambda *a, **k: fake)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}/versions/4.1.0",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    # Surviving version still ACTIVE in DB
    await db_session.refresh(v_new)
    assert v_new.status == DetectorVersionStatus.ACTIVE

    # Exactly one tag-level delete; the other tag still attached
    assert fake.calls == [
        ("delete_tag_or_artifact", "detectors", "rfdet", "4.1.0", "sha256:abc")
    ]
    assert fake.tags["sha256:abc"] == ["v4.1.0"]
