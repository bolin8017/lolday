"""Integration tests for D2.2 / R4 — AUTH_DEV_PERSONAS multi-persona dev auth.

Tests resolve_user_from_jwt directly (not through cf_access_user HTTP dep)
because the integration tier installs an X-Test-User-Email header override
on cf_access_user; the persona-header behaviour lives one level down in the
shared helper.
"""

from __future__ import annotations

import pytest
from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
from app.models.user import Role


@pytest.fixture(autouse=True)
def _enable_dev_mode(monkeypatch):
    """AUTH_DEV_MODE defaults to False; flip it on for every test in this file."""
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    yield


@pytest.mark.asyncio
async def test_persona_admin_resolves_admin_email_and_role(db_session) -> None:
    user = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header="admin"
    )
    assert user.email == "admin@dev.local"
    assert user.role == Role.ADMIN


@pytest.mark.asyncio
async def test_persona_developer_resolves_developer_email_and_role(db_session) -> None:
    user = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header="developer"
    )
    assert user.email == "dev@dev.local"
    assert user.role == Role.DEVELOPER


@pytest.mark.asyncio
async def test_persona_user_resolves_user_email_and_role(db_session) -> None:
    user = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header="user"
    )
    assert user.email == "user@dev.local"
    assert user.role == Role.USER


@pytest.mark.asyncio
async def test_unknown_persona_raises_cf_access_error(db_session) -> None:
    with pytest.raises(CfAccessAuthError) as exc:
        await resolve_user_from_jwt(
            db_session, None, log_context="test", persona_header="ghost"
        )
    assert "unknown persona" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_no_persona_falls_back_to_auth_dev_email(db_session, monkeypatch) -> None:
    """Absent X-Dev-Persona means use AUTH_DEV_EMAIL (backward compat)."""
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_EMAIL", "fallback@example.dev")
    user = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header=None
    )
    assert user.email == "fallback@example.dev"


@pytest.mark.asyncio
async def test_persona_switch_updates_role_on_subsequent_call(db_session) -> None:
    """User row created as admin; re-resolved as developer must flip role."""
    u_admin = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header="admin"
    )
    assert u_admin.role == Role.ADMIN

    u_dev = await resolve_user_from_jwt(
        db_session, None, log_context="test", persona_header="developer"
    )
    assert u_dev.role == Role.DEVELOPER
    # Different persona → different user row (separate email)
    assert u_admin.email != u_dev.email
