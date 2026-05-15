from uuid import UUID

import pytest

# ---------------------------------------------------------------------------
# #161 — PAT-in-URL regression. _clone_and_validate must NOT embed the PAT
# into the subprocess argv, and must scrub any PAT-shaped substring from the
# returned error body.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clone_does_not_pass_pat_in_argv(monkeypatch):
    """argv passed to ``asyncio.create_subprocess_exec`` must not contain the PAT."""
    from app.routers import detectors as dr

    captured: dict = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self):
            return None

        async def wait(self):
            return None

    async def fake_create(*args, **kwargs):
        captured["argv"] = list(args)
        captured["env"] = kwargs.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(dr.asyncio, "create_subprocess_exec", fake_create)

    async def fake_repo_accessible(owner, repo, pat):
        return True

    monkeypatch.setattr(dr, "check_repo_accessible", fake_repo_accessible)

    def fake_validate(path):
        return None

    monkeypatch.setattr(dr, "validate_repo_static", fake_validate)

    pat = "ghp_" + "A" * 36
    # Pre-create a fake pyproject.toml the helper reads after clone.
    import tempfile
    from pathlib import Path

    real_mkdtemp = tempfile.mkdtemp

    def fake_mkdtemp(prefix=""):
        d = real_mkdtemp(prefix=prefix)
        (Path(d) / "pyproject.toml").write_text(
            '[project]\nname = "fake"\ndescription = "x"\n'
        )
        return d

    monkeypatch.setattr(dr.tempfile, "mkdtemp", fake_mkdtemp)

    await dr._clone_and_validate("https://github.com/owner/repo.git", pat)

    # PAT must NOT appear anywhere in argv.
    for token in captured["argv"]:
        assert pat not in token, f"PAT leaked into argv: {token!r}"
    # PAT must be passed via env, not argv.
    assert captured["env"].get("GIT_TOKEN") == pat
    assert captured["env"].get("GIT_USER") == "x-token-auth"


@pytest.mark.asyncio
async def test_clone_failure_scrubs_pat_from_response(monkeypatch):
    """If git stderr accidentally surfaces a PAT, the HTTPException detail must redact it."""
    from app.routers import detectors as dr
    from fastapi import HTTPException

    pat = "ghp_" + "B" * 36
    leaked_stderr = (
        f"fatal: unable to access https://x-token-auth:{pat}@github.com/o/r.git/: "
        "Could not resolve host"
    ).encode()

    class _FakeProc:
        returncode = 1

        async def communicate(self):
            return b"", leaked_stderr

        def kill(self):
            return None

        async def wait(self):
            return None

    async def fake_create(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(dr.asyncio, "create_subprocess_exec", fake_create)

    async def fake_repo_accessible(owner, repo, pat):
        return True

    monkeypatch.setattr(dr, "check_repo_accessible", fake_repo_accessible)

    with pytest.raises(HTTPException) as excinfo:
        await dr._clone_and_validate("https://github.com/owner/repo.git", pat)

    message = excinfo.value.detail["message"]
    assert pat not in message
    assert "<redacted>" in message
    # The fine-grained pattern would also be scrubbed by the same regex.
    fg_pat = "github_pat_" + "C" * 82
    assert dr._scrub_github_pat(f"prefix {fg_pat} suffix") == "prefix <redacted> suffix"


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
async def test_delete_detector_helper_routes_through_tag_level_delete(
    async_client,
    detector_factory,
    version_factory,
    auth_owner_headers,
    monkeypatch,
):
    """`_delete_harbor_images` (called from delete_detector) must use
    tag-level delete to stay consistent with delete_version and to
    prevent shared-digest GC if a future detector grows multi-tag rows.
    """
    detector = await detector_factory(name="rfdet")
    await version_factory(
        detector_id=detector.id, git_tag="v1.0.0", image_digest="sha256:aaa"
    )
    await version_factory(
        detector_id=detector.id, git_tag="v2.0.0", image_digest="sha256:bbb"
    )

    fake = FakeHarborWithTags(tags={"sha256:aaa": ["v1.0.0"], "sha256:bbb": ["v2.0.0"]})
    monkeypatch.setattr("app.routers.detectors.HarborClient", lambda *a, **k: fake)
    monkeypatch.setattr("app.config.settings.HARBOR_ADMIN_PASSWORD", "x")

    resp = await async_client.delete(
        f"/api/v1/detectors/{detector.id}",
        headers=auth_owner_headers,
    )
    assert resp.status_code == 204

    method_names = {c[0] for c in fake.calls}
    assert method_names == {"delete_tag_or_artifact"}
    # Both versions cleaned up
    assert len(fake.calls) == 2
    assert (
        "delete_tag_or_artifact",
        "detectors",
        "rfdet",
        "v1.0.0",
        "sha256:aaa",
    ) in fake.calls
    assert (
        "delete_tag_or_artifact",
        "detectors",
        "rfdet",
        "v2.0.0",
        "sha256:bbb",
    ) in fake.calls


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

    list_resp = await async_client.get(
        f"/api/v1/detectors/{detector.id}/versions",
        headers=auth_owner_headers,
    )
    assert all(v["git_tag"] != "v1.0.0" for v in list_resp.json()["items"])


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
