import pytest


@pytest.mark.asyncio
async def test_list_models_empty(user_client):
    r = await user_client.get("/api/v1/models")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_get_model_version_by_id_404_missing(user_client):
    from uuid import uuid4

    r = await user_client.get(f"/api/v1/models/versions/{uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_models_versions_takes_precedence_over_name_route(user_client):
    """Regression: GET /models/versions resolves to the list endpoint.

    /models/versions and /models/{name} share the prefix. If route
    registration order broke, /{name=versions} would match first and
    return 404 'model not found'. Asserting 400 with "source_job_id" in
    the detail proves the more-specific list endpoint wins.
    """
    r = await user_client.get("/api/v1/models/versions")
    assert r.status_code == 400
    assert "source_job_id" in r.json()["detail"]


@pytest.mark.asyncio
async def test_model_version_read_includes_detector_fields(populated, alice_client):
    """ModelVersionRead must expose detector_id and detector_version_tag.

    These are needed by the frontend Submit Job form to derive the detector
    runtime from a chosen model artifact (mainstream MLOps inference UX).
    """
    r = await alice_client.get("/api/v1/models/alice/elf-rf/versions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "fixture should have at least one version"
    item = items[0]
    assert "detector_id" in item, "missing detector_id"
    assert "detector_version_tag" in item, "missing detector_version_tag"
    assert isinstance(item["detector_id"], str)
    assert isinstance(item["detector_version_tag"], str)
