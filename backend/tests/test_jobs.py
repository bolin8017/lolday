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
async def test_create_job_rejects_reserved_param_key(
    user_client, seed_detector_version, seed_dataset
):
    """H-5: reserved top-level namespace in user params must return 400."""
    dv_id = await seed_detector_version()
    train_ds = await seed_dataset(name="tr-reserved")
    test_ds = await seed_dataset(name="te-reserved")

    r = await user_client.post(
        "/api/v1/jobs",
        json={
            "type": "train",
            "detector_version_id": dv_id,
            "train_dataset_id": train_ds,
            "test_dataset_id": test_ds,
            "params": {"mlflow": {"tracking_uri": "http://evil"}},
        },
    )
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


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
    user_client, internal_client, seed_detector_version, seed_dataset
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

    # M-internal-split: /api/v1/internal/* is hosted by internal_app on
    # port 8001 in prod; tests target it via the ``internal_client`` fixture.
    # Calling without an Authorization header → 401 "missing bearer token";
    # calling with a non-matching bearer → 403/404 from require_job_token.
    r = await internal_client.get(f"/api/v1/internal/jobs/{jid}/config")
    assert r.status_code in (401, 403, 404)


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


# ---------------------------------------------------------------------------
# Phase 6 (Task F) — PATCH /jobs/{id} for admin priority bump
# ---------------------------------------------------------------------------


async def _seed_admin_with_queued_job(
    client,
    db_session,
    status: str = "queued_backend",
) -> tuple:
    """Helper: seed an admin user + a Job row with given status.

    Returns (admin_client_with_header, job_id_str).
    """
    import uuid as _uuid

    from app.models import Detector, DetectorVersion, Job, Role
    from app.models.detector import DetectorVersionStatus
    from app.models.job import JobStatus, JobType

    from tests.conftest import _MINIMAL_MANIFEST, _make_user

    admin_user = await _make_user("adm-patch@example.dev", role=Role.ADMIN)
    client.headers["x-test-user-email"] = "adm-patch@example.dev"

    det = Detector(
        name=f"patch-det-{_uuid.uuid4().hex[:6]}",
        display_name="patch-det",
        git_url="https://github.com/test/patch-det.git",
        owner_id=admin_user.id,
    )
    db_session.add(det)
    await db_session.flush()
    dv = DetectorVersion(
        detector_id=det.id,
        git_tag="v0.1.0",
        git_sha="a" * 40,
        harbor_image="harbor.harbor.svc:80/detectors/patch-det:v0.1.0",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
        manifest=_MINIMAL_MANIFEST,
    )
    db_session.add(dv)
    await db_session.flush()

    job = Job(
        type=JobType.TRAIN,
        status=JobStatus(status),
        detector_version_id=dv.id,
        owner_id=admin_user.id,
        resolved_config={},
        idempotency_key=_uuid.uuid4().hex,
        priority=0,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return client, str(job.id)


@pytest.mark.asyncio
async def test_patch_job_admin_queued_backend_updates_priority(
    client, db_session
) -> None:
    """Admin PATCH on a queued_backend job → 200, priority updated in response."""
    admin_client, job_id = await _seed_admin_with_queued_job(client, db_session)

    r = await admin_client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"priority": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == job_id
    assert body["priority"] == 5
    assert body["status"] == "queued_backend"


@pytest.mark.asyncio
async def test_patch_job_admin_idempotent(client, db_session) -> None:
    """Multiple PATCH calls with the same priority are idempotent — no error."""
    admin_client, job_id = await _seed_admin_with_queued_job(client, db_session)

    r1 = await admin_client.patch(f"/api/v1/jobs/{job_id}", json={"priority": 3})
    assert r1.status_code == 200, r1.text
    assert r1.json()["priority"] == 3

    r2 = await admin_client.patch(f"/api/v1/jobs/{job_id}", json={"priority": 3})
    assert r2.status_code == 200, r2.text
    assert r2.json()["priority"] == 3


@pytest.mark.asyncio
async def test_patch_job_priority_bump_total_counts_only_changes(
    client, db_session
) -> None:
    """Phase 6 follow-up A2: ``lolday_priority_bump_total`` increments once per
    actual priority change. Idempotent re-PATCHes don't inflate the signal —
    otherwise the metric would mis-report admin manual intervention frequency.
    """
    from prometheus_client import REGISTRY

    admin_client, job_id = await _seed_admin_with_queued_job(client, db_session)

    def _val() -> float:
        return REGISTRY.get_sample_value("lolday_priority_bump_total") or 0.0

    before = _val()

    r1 = await admin_client.patch(f"/api/v1/jobs/{job_id}", json={"priority": 5})
    assert r1.status_code == 200
    assert _val() == before + 1.0

    # Same value again — no-op, counter must NOT advance.
    r2 = await admin_client.patch(f"/api/v1/jobs/{job_id}", json={"priority": 5})
    assert r2.status_code == 200
    assert _val() == before + 1.0

    # Different value — counter advances.
    r3 = await admin_client.patch(f"/api/v1/jobs/{job_id}", json={"priority": 7})
    assert r3.status_code == 200
    assert _val() == before + 2.0


@pytest.mark.asyncio
async def test_patch_job_nonadmin_returns_403(client, db_session) -> None:
    """Non-admin user PATCH → 403 (admin-only endpoint)."""
    from app.models import Role

    from tests.conftest import _make_user

    # Seed the admin + job first using a separate client setup
    _admin_client, job_id = await _seed_admin_with_queued_job(client, db_session)

    # Now switch to a regular user
    await _make_user("user-patch@example.dev", role=Role.USER)
    client.headers["x-test-user-email"] = "user-patch@example.dev"

    r = await client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"priority": 5},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_patch_job_non_queued_backend_returns_422(client, db_session) -> None:
    """Admin PATCH on a running job → 422 with the canonical error message."""
    admin_client, job_id = await _seed_admin_with_queued_job(
        client, db_session, status="running"
    )

    r = await admin_client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"priority": 5},
    )
    assert r.status_code == 422, r.text
    assert (
        "priority cannot be changed after job has been submitted to Volcano"
        in r.json()["detail"]
    )


@pytest.mark.asyncio
async def test_patch_job_nonexistent_returns_404(client, db_session) -> None:
    """Admin PATCH on a non-existent job id → 404."""
    import uuid as _uuid

    from app.models import Role

    from tests.conftest import _make_user

    await _make_user("adm-404@example.dev", role=Role.ADMIN)
    client.headers["x-test-user-email"] = "adm-404@example.dev"

    fake_id = str(_uuid.uuid4())
    r = await client.patch(
        f"/api/v1/jobs/{fake_id}",
        json={"priority": 5},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# H-21: Volcano queue server-side enforcement regression tests
# ---------------------------------------------------------------------------


def test_build_volcano_job_manifest_queue_comes_from_arg():
    """H-21 unit regression: build_volcano_job_manifest must set spec.queue
    exclusively from the ``queue_name`` kwarg, which callers derive server-side
    via ensure_user_queue(job.owner_id).  Any attempt to inject a different
    queue name via params or other request-shaped input cannot reach spec.queue.
    """
    import uuid

    from app.models.job import JobType, ResourceProfile
    from app.services.job_spec import build_volcano_job_manifest
    from app.services.k8s import queue_name_for_user

    user_id = uuid.uuid4()
    server_queue = queue_name_for_user(user_id)  # lolday-u-<id12>
    attacker_queue = "lolday-u-evil-other-user"

    assert server_queue != attacker_queue, "test setup invariant"

    manifest = build_volcano_job_manifest(
        job_id=uuid.uuid4(),
        job_type=JobType.TRAIN,
        detector_image="harbor.harbor.svc:80/detectors/upxelfdet:v0.4.0",
        mlflow_experiment_id="1",
        mlflow_run_id="abc123",
        mlflow_tracking_uri="http://mlflow:5000",
        source_run_id=None,
        source_artifact_path=None,
        internal_events_url="http://backend:8000/api/v1/internal/jobs/fake/events",
        queue_name=server_queue,  # server-derived
        resource_profile=ResourceProfile.STANDARD,
    )

    actual_queue = manifest["spec"]["queue"]
    assert actual_queue == server_queue, (
        f"spec.queue should be the server-derived queue '{server_queue}', "
        f"got '{actual_queue}'"
    )
    assert actual_queue != attacker_queue, (
        "spec.queue must not contain the attacker-supplied queue name"
    )
    assert actual_queue.startswith("lolday-u-"), (
        f"spec.queue must follow the lolday-u-<id12> scheme, got '{actual_queue}'"
    )


def test_job_create_schema_silently_drops_queue_field():
    """H-21 schema regression: JobCreate has no 'queue' field.  Pydantic's
    default extra='ignore' means an attacker-supplied 'queue' key in the
    request body is silently dropped and never reaches the router or service
    layer.  This test also confirms the schema does not accidentally accept
    'queue' as a declared field.
    """
    import uuid

    from app.schemas.job import JobCreate

    # Verify no declared 'queue' field exists on the schema.
    assert "queue" not in JobCreate.model_fields, (
        "JobCreate must not have a 'queue' field — queue is server-derived only"
    )

    # Verify that an attacker-supplied 'queue' in the JSON body is silently
    # dropped (extra="ignore" is the Pydantic default; extra="forbid" would
    # raise, but either is safe as long as 'queue' is not a declared field).
    train_ds = uuid.uuid4()
    test_ds = uuid.uuid4()
    dv_id = uuid.uuid4()
    payload = {
        "type": "train",
        "detector_version_id": str(dv_id),
        "train_dataset_id": str(train_ds),
        "test_dataset_id": str(test_ds),
        "params": {},
        "queue": "lolday-u-evil-other-user",  # attacker-supplied
    }
    # Must not raise; 'queue' is silently dropped (or raises 422 — both are safe).
    try:
        parsed = JobCreate.model_validate(payload)
        # If we reach here: 'queue' was silently dropped (extra="ignore").
        assert not hasattr(parsed, "queue"), (
            "JobCreate instance must not expose a 'queue' attribute"
        )
    except Exception as exc:  # deliberate broad catch for 422-equiv validation
        # Pydantic ValidationError means extra="forbid" is in effect — also safe.
        assert "queue" in str(exc).lower(), (
            "Unexpected exception not related to 'queue' field"
        )
