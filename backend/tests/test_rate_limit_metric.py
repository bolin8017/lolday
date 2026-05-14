"""RATE_LIMIT_HITS_TOTAL increments when rate_limit_user / rate_limit_ip raises 429."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from prometheus_client import REGISTRY


def _read(metric: str, **labels) -> float:
    v = REGISTRY.get_sample_value(metric, labels=labels)
    return 0.0 if v is None else v


async def test_rate_limit_user_increments_metric_when_over_cap():
    from app.models import Role, User
    from app.services.rate_limit import rate_limit_user

    user = User(
        id=uuid.uuid4(), email="a@b", role=Role.USER, handle="h", display_name="d"
    )
    dep = rate_limit_user("test_prefix_a", limit=1, window_seconds=60)

    before = _read("lolday_rate_limit_hits_total", prefix="test_prefix_a")

    with patch("app.services.rate_limit.check_rate", new=AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as ei:
            await dep(user=user)
        assert ei.value.status_code == 429

    after = _read("lolday_rate_limit_hits_total", prefix="test_prefix_a")
    assert after - before == pytest.approx(1.0)


async def test_rate_limit_user_does_not_increment_when_under_cap():
    from app.models import Role, User
    from app.services.rate_limit import rate_limit_user

    user = User(
        id=uuid.uuid4(), email="a@b", role=Role.USER, handle="h", display_name="d"
    )
    dep = rate_limit_user("test_prefix_b", limit=10, window_seconds=60)

    before = _read("lolday_rate_limit_hits_total", prefix="test_prefix_b")

    with patch("app.services.rate_limit.check_rate", new=AsyncMock(return_value=True)):
        await dep(user=user)

    after = _read("lolday_rate_limit_hits_total", prefix="test_prefix_b")
    assert after == before
