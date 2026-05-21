"""Direct unit tests for `app.deps` dependency helpers.

The full integration tier already exercises every router-attached
dependency end-to-end, but several failure branches in
`load_detector` and `require_job_token` are async + nested inside
HTTPException raises that the CTracer-with-branch coverage engine
under-attributes (see `.claude/rules/testing.md` §14 for the asyncio
re-entry artifact). Direct unit tests here pin the contract
explicitly so a regression in the deny path flips the suite red.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from app.deps import load_detector, require_job_token
from app.models import Job, Role
from app.models.detector import Detector
from app.models.job import JobStatus, JobType
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_load_detector_returns_detector_when_present(db_session, seed_user):
    """Happy path — `load_detector` returns the row when it exists and is
    not soft-deleted. Pinned alongside the failure branches so the
    helper's contract is fully testable without going through a route.
    """
    det = Detector(
        name="dep-happy",
        display_name="dep-happy",
        git_url="https://github.com/x/dh.git",
        owner_id=seed_user.id,
    )
    db_session.add(det)
    await db_session.commit()
    out = await load_detector(detector_id=det.id, session=db_session)
    assert out.id == det.id


@pytest.mark.asyncio
async def test_load_detector_raises_404_for_missing(db_session):
    """`load_detector` raises 404 when the detector_id has no row.
    Covers deps.py:44-45 (the `d is None` branch).
    """
    missing_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await load_detector(detector_id=missing_id, session=db_session)
    assert exc.value.status_code == 404
    assert exc.value.detail == "detector not found"


@pytest.mark.asyncio
async def test_load_detector_raises_404_for_soft_deleted(db_session, seed_user):
    """`load_detector` treats a soft-deleted detector (deleted_at != None)
    as 404 — the row still exists in the DB but is invisible to the API.
    Covers the second clause of deps.py:44.
    """
    from datetime import UTC, datetime

    det = Detector(
        name="dep-deleted",
        display_name="dep-deleted",
        git_url="https://github.com/x/dd.git",
        owner_id=seed_user.id,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(det)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await load_detector(detector_id=det.id, session=db_session)
    assert exc.value.status_code == 404
    assert exc.value.detail == "detector not found"


def _make_job(
    *,
    owner_id: uuid.UUID,
    status: JobStatus = JobStatus.RUNNING,
    token_hash: str | None = None,
) -> Job:
    """Build a minimal Job row for require_job_token coverage."""
    from datetime import UTC, datetime

    return Job(
        id=uuid.uuid4(),
        type=JobType.TRAIN,
        status=status,
        detector_version_id=uuid.uuid4(),
        train_dataset_id=uuid.uuid4(),
        test_dataset_id=uuid.uuid4(),
        owner_id=owner_id,
        resolved_config={},
        mlflow_experiment_id="42",
        mlflow_run_id=f"run-{uuid.uuid4().hex[:8]}",
        idempotency_key=uuid.uuid4().hex,
        token_hash=token_hash,
        k8s_job_name=f"job-{uuid.uuid4().hex[:8]}",
        started_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_require_job_token_missing_bearer_returns_401(db_session, seed_user):
    """No `Authorization: Bearer ...` header → 401. Covers the format-check
    at deps.py:79-80.
    """
    job = _make_job(owner_id=seed_user.id, token_hash="a" * 64)
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(job_id=job.id, session=db_session, authorization=None)
    assert exc.value.status_code == 401
    assert "bearer" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_require_job_token_malformed_authz_returns_401(db_session, seed_user):
    """Authz header without the `Bearer ` prefix → 401. Same branch as
    missing header (deps.py:79-80), different shape.
    """
    job = _make_job(owner_id=seed_user.id, token_hash="a" * 64)
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(
            job_id=job.id, session=db_session, authorization="Basic xyz"
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_job_token_missing_job_returns_404(db_session):
    """Unknown job_id → 404. Covers deps.py:83-84 (`job is None` branch)."""
    missing_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(
            job_id=missing_id,
            session=db_session,
            authorization="Bearer tok",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_job_token_null_token_hash_returns_404(db_session, seed_user):
    """A job whose `token_hash` was cleared on terminal transition (H-20
    invalidation) MUST surface as 404 — never accept a stale token even
    if it'd otherwise verify. Covers the second clause of deps.py:83.
    """
    job = _make_job(owner_id=seed_user.id, token_hash=None)
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(
            job_id=job.id, session=db_session, authorization="Bearer tok"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_job_token_terminal_status_returns_404(db_session, seed_user):
    """A job in a terminal status (SUCCEEDED / FAILED / etc.) MUST be
    rejected as 404 even with a still-populated `token_hash`. Defence
    in depth on top of the H-20 token clear. Covers deps.py:85-86.
    """
    token = "tok-value"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    job = _make_job(
        owner_id=seed_user.id,
        status=JobStatus.SUCCEEDED,
        token_hash=token_hash,
    )
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(
            job_id=job.id,
            session=db_session,
            authorization=f"Bearer {token}",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_require_job_token_wrong_token_returns_403(db_session, seed_user):
    """Valid bearer header + active job + token that doesn't verify → 403.
    Covers deps.py:87-88.
    """
    real_token = "the-right-token"
    real_hash = hashlib.sha256(real_token.encode()).hexdigest()
    job = _make_job(
        owner_id=seed_user.id,
        status=JobStatus.RUNNING,
        token_hash=real_hash,
    )
    db_session.add(job)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await require_job_token(
            job_id=job.id,
            session=db_session,
            authorization="Bearer wrong-token",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_job_token_happy_path_returns_job(db_session, seed_user):
    """Valid bearer + active job + matching token → returns the Job row.
    Covers deps.py:89 (the success-path return).
    """
    from app.services.job_tokens import hash_token

    token = "tok-good"
    job = _make_job(
        owner_id=seed_user.id,
        status=JobStatus.RUNNING,
        token_hash=hash_token(token),
    )
    db_session.add(job)
    await db_session.commit()
    out = await require_job_token(
        job_id=job.id,
        session=db_session,
        authorization=f"Bearer {token}",
    )
    assert out.id == job.id


def test_role_hierarchy_service_token_is_negative():
    """`Role.SERVICE_TOKEN` MUST stay strictly less than any human role so
    a service-token caller falling through to `require_role(...)` lands
    on a clean 403, not a KeyError 500. Pinned per Phase 12.1 + root
    CLAUDE.md hard rule.
    """
    from app.deps import ROLE_HIERARCHY

    assert ROLE_HIERARCHY[Role.SERVICE_TOKEN] < 0
    assert ROLE_HIERARCHY[Role.SERVICE_TOKEN] < ROLE_HIERARCHY[Role.USER]
    assert ROLE_HIERARCHY[Role.SERVICE_TOKEN] < ROLE_HIERARCHY[Role.DEVELOPER]
    assert ROLE_HIERARCHY[Role.SERVICE_TOKEN] < ROLE_HIERARCHY[Role.ADMIN]
