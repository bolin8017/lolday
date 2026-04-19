import pytest


@pytest.mark.asyncio
async def test_list_models_empty(user_client):
    r = await user_client.get("/api/v1/models")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_model_versions_requires_name(user_client, seed_model_version):
    name, version = await seed_model_version()
    r = await user_client.get(f"/api/v1/models/{name}/versions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["mlflow_version"] == version


@pytest.mark.asyncio
async def test_transition_to_production_auto_archives_existing(
    user_client, seed_model_version
):
    name, v1 = await seed_model_version()
    name2, v2 = await seed_model_version(name=name)

    r = await user_client.post(
        f"/api/v1/models/{name}/versions/{v1}/transition",
        json={"to_stage": "Production", "comment": "first prod"},
    )
    assert r.status_code == 200

    r2 = await user_client.post(
        f"/api/v1/models/{name}/versions/{v2}/transition",
        json={"to_stage": "Production", "comment": "newer prod"},
    )
    assert r2.status_code == 200
    g = await user_client.get(f"/api/v1/models/{name}/versions/{v1}")
    assert g.json()["current_stage"] == "Archived"


@pytest.mark.asyncio
async def test_transition_denied_to_non_owner_developer(
    user_client, second_user_client, seed_model_version
):
    name, v = await seed_model_version()
    r = await second_user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging"},
    )
    assert r.status_code in (403, 422)


@pytest.mark.asyncio
async def test_transition_writes_audit_log(user_client, seed_model_version, db_session):
    from app.models.model_registry import ModelTransitionLog
    from sqlalchemy import select

    name, v = await seed_model_version()
    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging", "comment": "test"},
    )
    logs = (await db_session.execute(select(ModelTransitionLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].to_stage.value == "Staging"
    assert logs[0].comment == "test"


@pytest.mark.asyncio
async def test_delete_model_version_only_none_or_archived(
    user_client, seed_model_version
):
    name, v = await seed_model_version()
    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Staging"},
    )
    r = await user_client.delete(f"/api/v1/models/{name}/versions/{v}")
    assert r.status_code == 409

    await user_client.post(
        f"/api/v1/models/{name}/versions/{v}/transition",
        json={"to_stage": "Archived"},
    )
    r2 = await user_client.delete(f"/api/v1/models/{name}/versions/{v}")
    assert r2.status_code == 204
