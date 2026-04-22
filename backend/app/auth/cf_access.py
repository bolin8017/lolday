"""Cloudflare Access JWT verification.

Cloudflare Access forwards a signed JWT in the `Cf-Access-Jwt-Assertion`
request header for every request that has passed its identity-aware proxy.
The app verifies this JWT against Cloudflare's JWKS endpoint and trusts
the contained claims as the user identity.
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import Role, User

logger = logging.getLogger(__name__)


_REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss", "email"]


def verify_cf_token(
    token: str,
    signing_key,
    expected_aud: str,
    expected_iss: str,
) -> dict[str, Any]:
    claims = pyjwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=expected_aud,
        issuer=expected_iss,
        options={"require": _REQUIRED_CLAIMS},
    )
    # PyJWT defaults to accepting a list-shaped `aud` as long as our expected
    # value is a member. That would let a multi-aud token minted for another
    # app in the same Cloudflare account authenticate here. Cloudflare Access
    # today emits string-aud, so we just require exact string equality.
    if claims.get("aud") != expected_aud:
        raise pyjwt.InvalidAudienceError(
            f"aud must equal {expected_aud!r}, got {claims.get('aud')!r}"
        )
    return claims


def _sso_sentinel_password() -> str:
    # SSO users never log in via password. Store a syntactically-invalid
    # hash so any accidental verify() call fails closed.
    return f"!sso_only!{secrets.token_urlsafe(16)}"


async def get_or_create_user_by_email(session: AsyncSession, email: str) -> User:
    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    user = User(
        email=email,
        hashed_password=_sso_sentinel_password(),
        role=Role.USER,
        display_name=email.split("@", 1)[0],
        is_active=True,
        is_verified=True,
    )
    session.add(user)
    # Commit (not flush) so the INSERT survives the request-scope session
    # closing without an explicit commit. fastapi-users style dependencies
    # don't auto-commit; new users would otherwise rollback into the void
    # on first visit and never make it into /admin/users.
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        return existing
    await session.refresh(user)
    return user


@lru_cache(maxsize=1)
def _get_jwks_client() -> pyjwt.PyJWKClient:
    url = f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"
    return pyjwt.PyJWKClient(
        url,
        lifespan=settings.CF_ACCESS_JWKS_CACHE_TTL_SECONDS,
        cache_jwk_set=True,
    )


async def cf_access_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """FastAPI dependency: resolve the current user from the Cloudflare JWT.

    Returned `User` is attached to the request-scoped AsyncSession. Callers
    must not trigger lazy-load on relationships (e.g. `user.detector_set`)
    outside this session — AsyncSession raises `MissingGreenlet` on implicit
    lazy loads. If a router needs related rows, either query them explicitly
    or use `selectinload()` at query time.
    """
    if settings.AUTH_DEV_MODE:
        if not settings.AUTH_DEV_EMAIL:
            raise HTTPException(500, "AUTH_DEV_MODE enabled but AUTH_DEV_EMAIL empty")
        return await get_or_create_user_by_email(session, settings.AUTH_DEV_EMAIL)

    token = request.headers.get("cf-access-jwt-assertion")
    if not token:
        cf_hdrs = sorted(k for k in request.headers.keys() if k.lower().startswith("cf-"))
        logger.warning(
            "cf_access_user 401 path=%s: missing Cf-Access-Jwt-Assertion. cf-* headers present: %s",
            request.url.path, cf_hdrs,
        )
        raise HTTPException(401, "missing Cf-Access-Jwt-Assertion header")

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
    except pyjwt.PyJWKClientError as e:
        logger.warning("cf_access_user 401 path=%s: JWKS lookup failed: %s", request.url.path, e)
        raise HTTPException(401, f"jwks lookup failed: {e}") from e

    try:
        claims = verify_cf_token(
            token=token,
            signing_key=signing_key,
            expected_aud=settings.CF_ACCESS_APP_AUD,
            expected_iss=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        )
    except pyjwt.InvalidTokenError as e:
        # Log claim peek (aud/iss/email only) without logging full token
        try:
            unverified = pyjwt.decode(token, options={"verify_signature": False})
            peek = {k: unverified.get(k) for k in ("aud", "iss", "email", "exp")}
        except Exception:
            peek = "unparseable"
        logger.warning(
            "cf_access_user 401 path=%s: JWT invalid: %s. expected_aud=%s expected_iss=%s claims_peek=%s",
            request.url.path, e, settings.CF_ACCESS_APP_AUD,
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}", peek,
        )
        raise HTTPException(401, f"invalid Cloudflare Access token: {e}") from e

    return await get_or_create_user_by_email(session, claims["email"])
