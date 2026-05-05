import pytest


@pytest.mark.asyncio
async def test_create_train_job_happy_path(
    user_client, seed_detector_version, seed_dataset
):
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
    # Phase 6 (Task E): POST /jobs now writes queued_backend; dispatch is
    # deferred to the fifo_scheduler reconciler.
    assert body["status"] == "queued_backend"
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
async def test_create_evaluate_requires_source_model(
    user_client, seed_detector_version, seed_dataset
):
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
async def test_idempotency_duplicate_submission(
    user_client, seed_detector_version, seed_dataset
):
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
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 1},
        },
    )
    assert r1.status_code == 202

    r2 = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 2},
        },
    )
    assert r2.status_code == 429


@pytest.mark.asyncio
async def test_list_jobs_owner_scoped(
    user_client, second_user_client, seed_detector_version, seed_dataset
):
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="tr")
    te = await seed_dataset(name="te")
    await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {"seed": 1},
        },
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
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
        },
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
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
        },
    )
    jid = cr.json()["id"]

    r = await user_client.get(f"/api/v1/internal/jobs/{jid}/config")
    # user_client sends a JWT as Bearer token; require_job_token rejects it
    # with 403 (wrong token) since the JWT doesn't match the stored job token hash
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Phase 6 (Task E) — POST /jobs writes queued_backend + priority enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_default_priority_is_zero(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """POST /jobs without a priority field → job persisted with priority=0."""
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="pr-tr")
    te = await seed_dataset(name="pr-te")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued_backend"
    assert body["priority"] == 0


@pytest.mark.asyncio
async def test_create_job_nonadmin_priority_nonzero_returns_403(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """Non-admin user submitting priority != 0 → 403 (admin-only field)."""
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="pr403-tr")
    te = await seed_dataset(name="pr403-te")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
            "priority": 5,
        },
    )
    assert r.status_code == 403, r.text
    assert "admin-only" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_job_nonadmin_priority_zero_is_allowed(
    user_client, seed_detector_version, seed_dataset
) -> None:
    """Non-admin user explicitly submitting priority=0 → 202 (zero is the
    default and does not trigger the admin-only guard)."""
    dv_id = await seed_detector_version()
    tr = await seed_dataset(name="pr0-tr")
    te = await seed_dataset(name="pr0-te")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": tr,
            "test_dataset_id": te,
            "params": {},
            "priority": 0,
        },
    )
    assert r.status_code == 202, r.text
    assert r.json()["priority"] == 0


@pytest.mark.asyncio
async def test_create_job_admin_priority_nonzero_succeeds(client, db_session) -> None:
    """Admin user submitting priority=5 → 202, job created with priority=5.

    Uses ``db_session`` for ORM-level seeding so the admin client's
    X-Test-User-Email header is not overwritten by the ``user_client``
    dependency that ``seed_dataset`` / ``seed_detector_version`` pull in.
    """
    import uuid

    from app.models import Detector, DetectorVersion, Role
    from app.models.detector import DetectorVersionStatus

    from tests.conftest import _MINIMAL_MANIFEST, _make_user

    admin_user = await _make_user("adm@example.dev", role=Role.ADMIN)
    client.headers["x-test-user-email"] = "adm@example.dev"

    # Seed a dataset via HTTP using the admin client
    from pathlib import Path

    fixture_csv = (
        Path(__file__).parent / "fixtures" / "sample_dataset.csv"
    ).read_text()

    tr_resp = await client.post(
        "/api/v1/datasets", json={"name": "adm-tr", "csv_content": fixture_csv}
    )
    assert tr_resp.status_code == 201, tr_resp.text
    te_resp = await client.post(
        "/api/v1/datasets", json={"name": "adm-te", "csv_content": fixture_csv}
    )
    assert te_resp.status_code == 201, te_resp.text

    # Seed a detector version via ORM (admin is the owner)
    det = Detector(
        name=f"adm-det-{uuid.uuid4().hex[:6]}",
        display_name="adm-det",
        git_url="https://github.com/test/adm-det.git",
        owner_id=admin_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image="harbor.harbor.svc:80/detectors/adm-det:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=_MINIMAL_MANIFEST,
    )
    db_session.add(dv)
    await db_session.commit()

    r = await client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": str(dv.id),
            "train_dataset_id": tr_resp.json()["id"],
            "test_dataset_id": te_resp.json()["id"],
            "params": {},
            "priority": 5,
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued_backend"
    assert body["priority"] == 5
