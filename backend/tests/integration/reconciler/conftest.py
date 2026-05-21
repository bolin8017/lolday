import uuid

import pytest_asyncio
from app.models import Role, User
from app.models.detector import Detector, DetectorVersion, DetectorVersionStatus

from tests.conftest import test_session_maker


@pytest_asyncio.fixture
async def reconciler_owner() -> uuid.UUID:
    """Seed a User row and return its UUID.

    Reconciler tests insert Detector / DetectorBuild rows that FK back to
    user.id. Before issue #530 they used `owner_id=uuid4()` and survived
    only because the SQLite FK pragma leaked OFF across tests. Now FK is
    enforced per checkout — every test that touches `detector.owner_id`
    or `detector_build.triggered_by_id` must seed a real user.
    """
    async with test_session_maker() as session:
        user = User(
            email=f"recon-{uuid.uuid4().hex[:8]}@example.dev",
            handle=f"recon{uuid.uuid4().hex[:8]}",
            role=Role.DEVELOPER,
            display_name="reconciler test owner",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


@pytest_asyncio.fixture
async def reconciler_detector(reconciler_owner: uuid.UUID) -> Detector:
    """Seed a Detector row owned by `reconciler_owner` and return it.

    Tests that need to insert DetectorBuild rows but don't care about the
    parent Detector's specifics can FK to this fixture's `id`. Use
    `reconciler_owner` directly when only a User FK is needed.
    """
    suffix = uuid.uuid4().hex[:8]
    async with test_session_maker() as session:
        detector = Detector(
            name=f"reconciler-det-{suffix}",
            display_name=f"reconciler det {suffix}",
            git_url=f"https://github.com/example/det-{suffix}.git",
            owner_id=reconciler_owner,
        )
        session.add(detector)
        await session.commit()
        await session.refresh(detector)
        return detector


@pytest_asyncio.fixture
async def reconciler_detector_version(
    reconciler_detector: Detector,
) -> DetectorVersion:
    """Seed a DetectorVersion row for the `reconciler_detector` fixture.

    Job rows FK to `detector_version.id`. Tests that build a Job in the
    reconciler integration tier need this fixture in their signature so
    the Job insert satisfies SQLite FK enforcement after the pool-leak
    fix in `tests/conftest.py` (`connect` → `checkout` listener).
    """
    suffix = uuid.uuid4().hex[:8]
    async with test_session_maker() as session:
        dv = DetectorVersion(
            detector_id=reconciler_detector.id,
            git_tag=f"v0.0.{suffix[:4]}",
            git_sha=suffix * 5,
            harbor_image=f"harbor/{reconciler_detector.name}:{suffix}",
            image_digest=f"sha256:{suffix}{suffix}",
            status=DetectorVersionStatus.ACTIVE,
        )
        session.add(dv)
        await session.commit()
        await session.refresh(dv)
        return dv
