"""Smoke: confirms the testcontainers Postgres fixture spins up and
yields a usable session."""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.heavy


@pytest.mark.asyncio
async def test_real_pg_session_returns_one(real_pg_session):
    result = await real_pg_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
