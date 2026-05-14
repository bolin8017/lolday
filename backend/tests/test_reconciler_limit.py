"""M-reconciler-limit: scan caps at 200 rows, orders oldest first, counter increments on cap-hit."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from prometheus_client import REGISTRY


def _read(metric: str, **labels) -> float:
    v = REGISTRY.get_sample_value(metric, labels=labels)
    return 0.0 if v is None else v


# ---------------------------------------------------------------------------
# Helpers: seed FK-valid parent rows
# ---------------------------------------------------------------------------


async def _seed_owner(db_session):
    """Insert a minimal User row; return its UUID."""
    from app.models import Role, User
    from app.services.user_handle import derive_handle_from_email, next_unique_handle
    from sqlalchemy import select

    email = f"reconciler-limit-{uuid.uuid4().hex[:8]}@test.dev"
    existing_handles = set(
        (await db_session.execute(select(User.handle))).scalars().all()
    )
    handle = next_unique_handle(
        derive_handle_from_email(email), existing=existing_handles
    )
    user = User(
        email=email,
        handle=handle,
        role=Role.USER,
        display_name="reconciler-limit-test",
    )
    db_session.add(user)
    await db_session.flush()
    return user.id


async def _seed_detector(db_session, owner_id):
    """Insert a Detector row; return its UUID."""
    from app.models.detector import Detector

    det = Detector(
        name=f"det-{uuid.uuid4().hex[:8]}",
        display_name="test-detector",
        git_url=f"https://github.com/test/det-{uuid.uuid4().hex[:6]}.git",
        owner_id=owner_id,
    )
    db_session.add(det)
    await db_session.flush()
    return det.id


async def _seed_detector_version(db_session, detector_id):
    """Insert a DetectorVersion row; return its UUID."""
    from app.models.detector import DetectorVersion, DetectorVersionStatus

    dv = DetectorVersion(
        detector_id=detector_id,
        git_tag=f"v{uuid.uuid4().hex[:4]}",
        git_sha="a" * 40,
        harbor_image=f"harbor.harbor.svc:80/detectors/det:v1-{uuid.uuid4().hex[:6]}",
        image_digest="sha256:" + "a" * 64,
        status=DetectorVersionStatus.ACTIVE,
    )
    db_session.add(dv)
    await db_session.flush()
    return dv.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_reconciler_caps_job_scan_at_200_oldest_first(db_session, monkeypatch):
    """Seed 250 non-terminal Jobs; iteration scans 200 oldest by submitted_at."""
    from app.models import Job, JobStatus
    from app.reconciler.loop import _scan_jobs

    # Seed FK-valid parent rows.
    owner_id = await _seed_owner(db_session)
    detector_id = await _seed_detector(db_session, owner_id)
    dv_id = await _seed_detector_version(db_session, detector_id)

    base = datetime.now(tz=UTC) - timedelta(hours=10)
    for i in range(250):
        j = Job(
            id=uuid.uuid4(),
            type="train",
            owner_id=owner_id,
            detector_version_id=dv_id,
            status=JobStatus.QUEUED_BACKEND,
            submitted_at=base + timedelta(seconds=i),
            resolved_config={},
            idempotency_key=uuid.uuid4().hex,
        )
        db_session.add(j)
    await db_session.commit()

    before = _read("lolday_reconciler_scan_truncated_total", kind="job")
    rows = await _scan_jobs(db_session, limit=200)
    assert len(rows) == 200
    # SQLite strips tzinfo on round-trip; compare naive timestamps.
    assert rows[0].submitted_at.replace(tzinfo=None) == base.replace(tzinfo=None)
    assert rows[199].submitted_at.replace(tzinfo=None) == (
        base + timedelta(seconds=199)
    ).replace(tzinfo=None)
    after = _read("lolday_reconciler_scan_truncated_total", kind="job")
    assert after - before == pytest.approx(1.0)


async def test_reconciler_caps_build_scan_at_200_oldest_first(db_session):
    """Same as above for DetectorBuild keyed by started_at."""
    from app.models.detector import DetectorBuild
    from app.reconciler.builds import IN_FLIGHT
    from app.reconciler.loop import _scan_builds

    # Seed FK-valid parent rows.
    owner_id = await _seed_owner(db_session)
    detector_id = await _seed_detector(db_session, owner_id)

    status_val = next(iter(IN_FLIGHT))
    base = datetime.now(tz=UTC) - timedelta(hours=5)
    for i in range(250):
        b = DetectorBuild(
            id=uuid.uuid4(),
            detector_id=detector_id,
            triggered_by_id=owner_id,
            git_tag="v1",
            status=status_val,
            started_at=base + timedelta(seconds=i),
        )
        db_session.add(b)
    await db_session.commit()

    before = _read("lolday_reconciler_scan_truncated_total", kind="build")
    rows = await _scan_builds(db_session, limit=200)
    assert len(rows) == 200
    # SQLite strips tzinfo on round-trip; compare naive timestamps.
    assert rows[0].started_at.replace(tzinfo=None) == base.replace(tzinfo=None)
    after = _read("lolday_reconciler_scan_truncated_total", kind="build")
    assert after - before == pytest.approx(1.0)


async def test_reconciler_counter_does_not_increment_below_cap(db_session):
    """Seed 50 rows; scan returns 50; counter does NOT increment."""
    from app.models import Job, JobStatus
    from app.reconciler.loop import _scan_jobs

    # Seed FK-valid parent rows.
    owner_id = await _seed_owner(db_session)
    detector_id = await _seed_detector(db_session, owner_id)
    dv_id = await _seed_detector_version(db_session, detector_id)

    base = datetime.now(tz=UTC) - timedelta(hours=20)
    for i in range(50):
        j = Job(
            id=uuid.uuid4(),
            type="train",
            owner_id=owner_id,
            detector_version_id=dv_id,
            status=JobStatus.QUEUED_BACKEND,
            submitted_at=base + timedelta(seconds=i),
            resolved_config={},
            idempotency_key=uuid.uuid4().hex,
        )
        db_session.add(j)
    await db_session.commit()

    before = _read("lolday_reconciler_scan_truncated_total", kind="job")
    rows = await _scan_jobs(db_session, limit=200)
    assert len(rows) == 50
    after = _read("lolday_reconciler_scan_truncated_total", kind="job")
    assert after == before
