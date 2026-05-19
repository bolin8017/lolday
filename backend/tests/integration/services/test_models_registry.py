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


@pytest.mark.asyncio
async def test_model_version_read_is_runnable_tracks_dv_status(
    populated, alice_client, db_session
):
    """`is_runnable` mirrors `DetectorVersion.status == ACTIVE`.

    The flag closes architecture.md §10 #22: the frontend's InferenceSubForm
    reads it to grey out model-version dropdown entries whose training
    DetectorVersion has been retired, so the user sees the constraint before
    they try to submit. Backend behaviour: ACTIVE → True, anything else
    (RETENTION_PRUNED / DELETED) → False.
    """
    from app.models import DetectorVersion
    from app.models.detector import DetectorVersionStatus
    from sqlalchemy import select

    # Baseline — every populated fixture row defaults to ACTIVE, so every
    # ModelVersion in /models/alice/elf-rf/versions starts as runnable.
    r = await alice_client.get("/api/v1/models/alice/elf-rf/versions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items, "fixture should have at least one version"
    for item in items:
        assert item["is_runnable"] is True, (
            f"version {item['mlflow_version']} should be runnable while DV is ACTIVE"
        )

    # Retire the shared DV (RETENTION_PRUNED is the reconciler-driven path).
    # All ModelVersions that point at this DV must flip to is_runnable=False.
    dv = (
        (
            await db_session.execute(
                select(DetectorVersion).where(DetectorVersion.git_tag == "v1")
            )
        )
        .scalars()
        .first()
    )
    assert dv is not None
    dv.status = DetectorVersionStatus.RETENTION_PRUNED
    await db_session.commit()

    r2 = await alice_client.get("/api/v1/models/alice/elf-rf/versions")
    assert r2.status_code == 200
    items2 = r2.json()["items"]
    assert items2
    for item in items2:
        assert item["is_runnable"] is False, (
            f"version {item['mlflow_version']} should NOT be runnable after DV retire"
        )

    # Single-version endpoint (`GET /models/versions/{id}`) must report the
    # same value — the InferenceSubForm hits both endpoints depending on the
    # flow path, so they have to stay in lock-step.
    one = await alice_client.get(f"/api/v1/models/versions/{items2[0]['id']}")
    assert one.status_code == 200
    assert one.json()["is_runnable"] is False
