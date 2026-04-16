import pytest
import pytest_asyncio
from uuid import UUID

from app.models.detector import DetectorBuild, DetectorBuildStatus


@pytest_asyncio.fixture
async def seed_build_with_token(auth_client_developer, seed_detector):
    """Create a DetectorBuild directly in DB with VALIDATING status + known token."""
    from tests.conftest import test_session_maker
    from sqlalchemy import select
    from app.models import User
    async with test_session_maker() as session:
        # Look up actual user to satisfy FK constraint
        res = await session.execute(select(User).where(User.email == "dev@example.dev"))
        user = res.scalar_one()
        build = DetectorBuild(
            detector_id=UUID(seed_detector),
            git_tag="v0.1.0",
            triggered_by_id=user.id,
            status=DetectorBuildStatus.VALIDATING,
            build_token="btok_testtoken",
        )
        session.add(build)
        await session.commit()
        await session.refresh(build)
        return (build.id, build.build_token)


@pytest.mark.asyncio
async def test_schema_callback_with_valid_token(client, seed_build_with_token):
    build_id, token = seed_build_with_token
    resp = await client.post(
        f"/api/v1/internal/builds/{build_id}/schema",
        json={"schema": {"type": "object", "properties": {}}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_schema_callback_rejects_bad_token(client, seed_build_with_token):
    build_id, _ = seed_build_with_token
    resp = await client.post(
        f"/api/v1/internal/builds/{build_id}/schema",
        json={"schema": {}},
        headers={"Authorization": "Bearer bad"},
    )
    assert resp.status_code == 401
