import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_train_job_happy_path(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    train_ds = await seed_dataset(name="tr-ds")
    test_ds = await seed_dataset(name="te-ds")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"seed": 42},
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] in ("pending", "preparing")
    assert body["type"] == "train"
    assert body["mlflow_run_id"]


@pytest.mark.asyncio
async def test_create_job_type_mismatch_rejected(user_client, seed_detector_version):
    dv_id = await seed_detector_version()
    r = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id, "params": {}},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_evaluate_requires_source_model(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    test_ds = await seed_dataset(name="te-ds")
    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "evaluate",
            "detector_version_id": dv_id,
            "test_dataset_id": test_ds,
            "params": {},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_duplicate_submission(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    payload = {
        "type": "train",
        "detector_version_id": dv_id,
        "train_dataset_id": tr,
        "test_dataset_id": te,
        "params": {"seed": 1},
    }
    r1 = await user_client.post("/api/v1/jobs", json=payload)
    assert r1.status_code == 202
    r2 = await user_client.post("/api/v1/jobs", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_concurrency_limit_enforced(
    user_client, seed_detector_version, seed_dataset, monkeypatch
):
    from app.config import settings
    monkeypatch.setattr(settings, "JOB_PER_USER_CONCURRENCY", 1)

    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")

    r1 = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 1}},
    )
    assert r1.status_code == 202

    r2 = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 2}},
    )
    assert r2.status_code == 429


@pytest.mark.asyncio
async def test_list_jobs_owner_scoped(user_client, second_user_client,
                                       seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {"seed": 1}},
    )
    r = await second_user_client.get("/api/v1/jobs")
    assert r.json()["total"] == 0


@pytest.mark.asyncio
async def test_cancel_job(user_client, seed_detector_version, seed_dataset):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    cr = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {}},
    )
    jid = cr.json()["id"]
    r = await user_client.post(f"/api/v1/jobs/{jid}/cancel")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_internal_config_endpoint_requires_token(
    user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    cr = await user_client.post(
        "/api/v1/jobs",
        json={"type": "train", "detector_version_id": dv_id,
              "train_dataset_id": tr, "test_dataset_id": te, "params": {}},
    )
    jid = cr.json()["id"]

    r = await user_client.get(f"/api/v1/internal/jobs/{jid}/config")
    # user_client sends a JWT as Bearer token; require_job_token rejects it
    # with 403 (wrong token) since the JWT doesn't match the stored job token hash
    assert r.status_code in (401, 403)
