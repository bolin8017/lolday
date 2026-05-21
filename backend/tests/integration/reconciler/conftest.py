import uuid

import pytest_asyncio
from app.models import Role, User
from app.models.detector import Detector

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
