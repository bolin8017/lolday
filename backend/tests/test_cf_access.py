"""Unit tests for Cloudflare Access JWT verification.

Tests the pure verification function (no network, no FastAPI context).
Uses an ephemeral RSA keypair generated per-test so we never need real
Cloudflare keys.
"""
import time
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def rsa_keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _sign(
    priv: rsa.RSAPrivateKey,
    claims: dict[str, Any],
    kid: str = "test-kid",
    algorithm: str = "RS256",
) -> str:
    return pyjwt.encode(claims, priv, algorithm=algorithm, headers={"kid": kid})


def _valid_claims() -> dict[str, Any]:
    now = int(time.time())
    return {
        "aud": "test-app-uid",
        "iss": "https://test.cloudflareaccess.com",
        "email": "alice@example.com",
        "sub": "cf-user-uuid-1",
        "iat": now,
        "exp": now + 300,
    }


def test_verify_cf_token_returns_claims_for_valid_token(rsa_keypair):
    from app.auth.cf_access import verify_cf_token

    token = _sign(rsa_keypair, _valid_claims())
    result = verify_cf_token(
        token=token,
        signing_key=rsa_keypair.public_key(),
        expected_aud="test-app-uid",
        expected_iss="https://test.cloudflareaccess.com",
    )
    assert result["email"] == "alice@example.com"
    assert result["sub"] == "cf-user-uuid-1"


def test_verify_cf_token_rejects_token_without_exp(rsa_keypair):
    """Tokens without an expiration are security risks (never expire)."""
    from app.auth.cf_access import verify_cf_token

    claims = _valid_claims()
    del claims["exp"]
    token = _sign(rsa_keypair, claims)

    with pytest.raises(pyjwt.MissingRequiredClaimError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


async def test_get_or_create_user_creates_new_row_with_defaults(db_session):
    """First visit by a new email auto-provisions a User with role=USER,
    is_active=true, and display_name derived from email local-part."""
    from sqlalchemy import select

    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import Role, User

    user = await get_or_create_user_by_email(db_session, "newbie@example.com")

    assert user.email == "newbie@example.com"
    assert user.role == Role.USER
    assert user.is_active is True
    assert user.display_name == "newbie"

    row = (
        await db_session.execute(select(User).where(User.email == "newbie@example.com"))
    ).scalar_one()
    assert row.id == user.id


async def test_get_or_create_user_returns_existing_row(db_session):
    """Subsequent visits re-use the existing User row without creating duplicates."""
    from sqlalchemy import func, select

    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import User

    a = await get_or_create_user_by_email(db_session, "returning@example.com")
    b = await get_or_create_user_by_email(db_session, "returning@example.com")

    assert a.id == b.id
    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.email == "returning@example.com")
        )
    ).scalar_one()
    assert count == 1


def _make_request(headers: list[tuple[bytes, bytes]] | None = None):
    from fastapi import Request

    return Request(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": headers or [],
            "query_string": b"",
        }
    )


async def test_cf_access_user_returns_user_for_valid_jwt(
    rsa_keypair, db_session, monkeypatch
):
    """Valid JWT header → User provisioned and returned (happy path)."""
    from app.auth import cf_access as cf
    from app.config import settings

    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")
    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)

    class _Key:
        def __init__(self, k):
            self.key = k

    class _FakeJWKS:
        def get_signing_key_from_jwt(self, token):
            return _Key(rsa_keypair.public_key())

    monkeypatch.setattr(cf, "_get_jwks_client", lambda: _FakeJWKS())

    token = _sign(rsa_keypair, _valid_claims() | {"iss": "https://test.cloudflareaccess.com"})
    req = _make_request(headers=[(b"cf-access-jwt-assertion", token.encode())])

    user = await cf.cf_access_user(request=req, session=db_session)

    assert user.email == "alice@example.com"
    assert user.role.value == "user"
    assert user.is_active is True


async def test_cf_access_user_raises_401_when_header_missing(db_session, monkeypatch):
    from fastapi import HTTPException

    from app.auth.cf_access import cf_access_user
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    req = _make_request()

    with pytest.raises(HTTPException) as exc:
        await cf_access_user(request=req, session=db_session)
    assert exc.value.status_code == 401


async def test_cf_access_user_dev_mode_bypasses_jwt(db_session, monkeypatch):
    """AUTH_DEV_MODE=true returns a synthetic user regardless of header state.

    This path is never used in production (helm values pin AUTH_DEV_MODE=false).
    """
    from app.auth.cf_access import cf_access_user
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    monkeypatch.setattr(settings, "AUTH_DEV_EMAIL", "dev@local")

    req = _make_request()
    user = await cf_access_user(request=req, session=db_session)

    assert user.email == "dev@local"
    assert user.role.value == "user"
