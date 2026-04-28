"""Tests for service-token user handling: skip Discord notify + friendly name.

Phase 12.1: CF Access service-token JWTs lack `email`, so cf_access.py
synthesises ``service-<common_name>@cf-access.local``. Those rows are
machine principals — Discord events for them dilute the channel and
are not actionable. We:

  (A) early-return from `_user_context` so every notify_* call skips,
  (B) override the auto-derived `display_name` to a human-friendly
      label, both at create time and via a one-shot migration.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.cf_access import get_or_create_user_by_email
from app.models import User
from app.reconciler import _fire_job_failed_notify, _user_context

SERVICE_TOKEN_EMAIL = "service-abc123def456.access@cf-access.local"
SERVICE_TOKEN_FRIENDLY_NAME = "Internal service token"


# ---- (A) skip notify --------------------------------------------------------


def test_user_is_service_token_property():
    """Service-token rows must self-identify so policy code can skip them."""
    svc = User(email=SERVICE_TOKEN_EMAIL, hashed_password="!", is_active=True)
    real = User(email="alice@example.com", hashed_password="!", is_active=True)
    assert svc.is_service_token is True
    assert real.is_service_token is False


@pytest.mark.asyncio
async def test_user_context_returns_none_for_service_token(db_session):
    """`_user_context` is the chokepoint every notify_* path passes through."""
    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        is_active=True,
        is_verified=True,
    )
    db_session.add(svc)
    await db_session.commit()
    await db_session.refresh(svc)

    assert await _user_context(db_session, svc.id) is None


@pytest.mark.asyncio
async def test_user_context_returns_tuple_for_real_user(db_session, seed_user):
    """Real users still get the (display_name, discord_id) tuple."""
    ctx = await _user_context(db_session, seed_user.id)
    assert ctx is not None
    name, discord_id = ctx
    assert isinstance(name, str)


@pytest.mark.asyncio
async def test_fire_job_failed_notify_skips_service_token(db_session, seed_job):
    """Integration: service-token-owned job failure must NOT call notify."""
    j = await seed_job()
    # Re-point owner to a freshly-created service-token user.
    svc = User(
        email=SERVICE_TOKEN_EMAIL,
        hashed_password="!",
        is_active=True,
        is_verified=True,
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
    local-part as display_name (it's a 64-char hex stamp humans can't read).
    """
    user = await get_or_create_user_by_email(db_session, SERVICE_TOKEN_EMAIL)
    assert user.display_name == SERVICE_TOKEN_FRIENDLY_NAME


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
        is_active=True,
        is_verified=True,
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
        is_active=True,
        is_verified=True,
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
