import pytest
from uuid import UUID


@pytest.mark.asyncio
async def test_get_version_legacy_null_manifest_returns_200(
    auth_client_developer, db_session, monkeypatch
):
    """Phase 13a A1: legacy versions built before maldet 1.1 have manifest=NULL.

    Schema must accept None; endpoint must return 200 with `manifest: null`.
    """
    from app.routers import detectors as dr
    from app.models import Detector, DetectorVersion
    from app.models.detector import DetectorVersionStatus

    # Fake the _clone_and_validate so detector registration works
    async def fake_meta(url, pat):
        return {"name": "legacy-det", "description": "demo", "display_name": "legacy-det"}

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
