"""Tests for service-token user handling: skip Discord notify + friendly name.

CF Access service-token JWTs lack `email`, so cf_access.py synthesises
``service-<common_name>@cf-access.local``. Those rows are machine
principals — Discord events for them dilute the channel and are not
actionable. We:

  (A) early-return from `_user_context` so every notify_* call skips,
  (B) override the auto-derived `display_name` to a human-friendly
      label, both at create time and via a one-shot migration.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.cf_access import get_or_create_user_by_email
from app.models import Role, User
from app.reconciler import _fire_job_failed_notify, _user_context

SERVICE_TOKEN_EMAIL = "service-abc123def456.access@cf-access.local"
SERVICE_TOKEN_FRIENDLY_NAME = "Internal service token"


# ---- (A) skip notify --------------------------------------------------------


def test_user_is_service_token_property():
    """Service-token rows must self-identify so policy code can skip them."""
    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        role=Role.SERVICE_TOKEN,
    )
    real = User(
        email="alice@example.com",
        hashed_password="!",
        role=Role.USER,
    )
    assert svc.is_service_token is True
    assert real.is_service_token is False


def test_user_is_service_token_ignores_email_when_role_is_user():
    """Email-suffix collision must not flip is_service_token; role is the
    sole source of truth (regression guard for the refactor away from
    email-suffix probing)."""
    spoof = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        role=Role.USER,
    )
    assert spoof.is_service_token is False


@pytest.mark.asyncio
async def test_user_context_returns_none_for_service_token(db_session):
    """`_user_context` is the chokepoint every notify_* path passes through."""
    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        role=Role.SERVICE_TOKEN,
    )
    db_session.add(svc)
    await db_session.commit()
    await db_session.refresh(svc)

    assert await _user_context(db_session, svc.id) is None


@pytest.mark.asyncio
async def test_user_context_returns_dataclass_for_real_user(db_session, seed_user):
    """Real users still get a populated NotifyContext."""
    ctx = await _user_context(db_session, seed_user.id)
    assert ctx is not None
    assert isinstance(ctx.name, str)
    assert ctx.name  # non-empty


@pytest.mark.asyncio
async def test_fire_job_failed_notify_skips_service_token(db_session, seed_job):
    """Integration: service-token-owned job failure must NOT call notify."""
    j = await seed_job()
    # Re-point owner to a freshly-created service-token user.
    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        role=Role.SERVICE_TOKEN,
    )
    db_session.add(svc)
    await db_session.commit()
    await db_session.refresh(svc)
    j.owner_id = svc.id
    await db_session.commit()

    with patch("app.reconciler.notify_job_failed", new=AsyncMock()) as m:
        await _fire_job_failed_notify(db_session, j, "test_reason")
    assert m.await_count == 0, (
        f"notify_job_failed must be skipped for service token, "
        f"got {m.await_count} call(s)"
    )


# ---- (B) friendly display_name ---------------------------------------------


@pytest.mark.asyncio
async def test_service_token_create_uses_friendly_display_name(db_session):
    """Auto-creating a service-token user must NOT pin the raw email
    local-part as display_name (it's a 64-char hex stamp humans can't read),
    AND must set ``role=Role.SERVICE_TOKEN`` so subsequent notify policy
    works without a follow-up migration.
    """
    user = await get_or_create_user_by_email(db_session, SERVICE_TOKEN_EMAIL)
    assert user.display_name == SERVICE_TOKEN_FRIENDLY_NAME
    assert user.role == Role.SERVICE_TOKEN
    assert user.is_service_token is True


@pytest.mark.asyncio
async def test_real_user_create_sets_role_user(db_session):
    """Regression guard: only service-token rows get role=SERVICE_TOKEN."""
    user = await get_or_create_user_by_email(db_session, "alice@example.com")
    assert user.role == Role.USER
    assert user.is_service_token is False


@pytest.mark.asyncio
async def test_real_user_create_keeps_email_local_part_as_display_name(db_session):
    """Regression guard: only service-token rows get rewritten."""
    user = await get_or_create_user_by_email(db_session, "alice@example.com")
    assert user.display_name == "alice"


@pytest.mark.asyncio
async def test_existing_service_token_user_with_raw_name_is_renamed(db_session):
    """A row that already exists with the auto-derived raw display_name should
    be migrated to the friendly name on next get-or-create call (idempotent).
    """
    raw_local = SERVICE_TOKEN_EMAIL.split("@", 1)[0]
    existing = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!sso!",
        display_name=raw_local,  # what the old code wrote
    )
    db_session.add(existing)
    await db_session.commit()

    user = await get_or_create_user_by_email(db_session, SERVICE_TOKEN_EMAIL)
    assert user.id == existing.id  # same row, not a duplicate
    assert user.display_name == SERVICE_TOKEN_FRIENDLY_NAME


@pytest.mark.asyncio
async def test_existing_service_token_user_with_custom_name_is_left_alone(db_session):
    """If an admin manually set a custom display_name, don't clobber it."""
    custom = "Custom Bot Name"
    existing = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!sso!",
        display_name=custom,
    )
    db_session.add(existing)
    await db_session.commit()

    user = await get_or_create_user_by_email(db_session, SERVICE_TOKEN_EMAIL)
    assert user.display_name == custom


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
async def seed_job(db_session, seed_detector_version, seed_dataset, seed_user):
    """Insert a Job row owned by `seed_user` and return it."""
    from app.models.job import Job, JobStatus, JobType

    async def _seed():
        dv_id = await seed_detector_version(name=f"det-{uuid.uuid4().hex[:6]}")
        tr = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        te = await seed_dataset(name=f"ds-{uuid.uuid4().hex[:6]}")
        j = Job(
            type=JobType.TRAIN,
            status=JobStatus.RUNNING,
            detector_version_id=uuid.UUID(dv_id),
            train_dataset_id=uuid.UUID(tr),
            test_dataset_id=uuid.UUID(te),
            owner_id=seed_user.id,
            resolved_config={},
            mlflow_experiment_id="42",
            mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
            idempotency_key=uuid.uuid4().hex,
            token_hash="a" * 64,
            k8s_job_name=f"job-train-{uuid.uuid4().hex[:8]}",
        )
        db_session.add(j)
        await db_session.commit()
        await db_session.refresh(j)
        return j
    return _seed


# ---- (A) service-token build paths skip notify ------------------------------


@pytest.mark.asyncio
async def test_reconcile_build_manifest_missing_skips_service_token(db_session):
    """Drives reconcile_build into the `manifest_label_missing` path with
    a service-token-owned build and asserts notify_build_failed is NEVER
    awaited. This is the cheapest regression guard against a future
    typo that flips ``if ctx is not None:`` on one of the seven manifest-
    pipeline callsites inside reconcile_build.
    """
    from unittest.mock import AsyncMock, MagicMock, patch as mpatch

    from app.models import User
    from app.models.detector import (
        Detector,
        DetectorBuild,
        DetectorBuildStatus,
    )
    from app.reconciler import reconcile_build

    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        role=Role.SERVICE_TOKEN,
    )
    db_session.add(svc)
    await db_session.commit()
    await db_session.refresh(svc)

    detector = Detector(
        name="srvc-det",
        display_name="Srvc",
        git_url="https://github.com/x/y.git",
        owner_id=svc.id,
    )
    db_session.add(detector)
    await db_session.commit()
    await db_session.refresh(detector)

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag="v0.0.1",
        triggered_by_id=svc.id,
        k8s_job_name="build-srvc",
        status=DetectorBuildStatus.SCANNING,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)

    fake_job = MagicMock()
    fake_job.status.succeeded = 1
    fake_job.status.failed = 0

    from app.services.harbor import ScanResult, ScanStatus

    with mpatch("app.reconciler.batch_v1") as bv, \
         mpatch("app.reconciler.HarborClient") as hc, \
         mpatch("app.reconciler.notify_build_failed", new=AsyncMock()) as m:
        bv.return_value.read_namespaced_job.return_value = fake_job
        hc.return_value.get_artifact_digest = AsyncMock(return_value="sha256:abc")
        hc.return_value.get_scan = AsyncMock(
            return_value=ScanResult(ScanStatus.SUCCESS, 0, 0, 0, 0)
        )
        # Drives the manifest_label_missing branch — labels come back
        # without `io.maldet.manifest`.
        hc.return_value.get_image_labels = AsyncMock(return_value={})
        await reconcile_build(db_session, build)

    assert m.await_count == 0, (
        "service-token build must NOT trigger notify_build_failed; "
        f"got {m.await_count} call(s)"
    )

    await db_session.refresh(build)
    assert build.status == DetectorBuildStatus.FAILED, build.status
    assert build.failure_reason == "manifest_label_missing", build.failure_reason
