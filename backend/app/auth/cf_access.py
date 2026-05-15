"""Cloudflare Access JWT verification.

Cloudflare Access forwards a signed JWT in the `Cf-Access-Jwt-Assertion`
request header for every request that has passed its identity-aware proxy.
The app verifies this JWT against Cloudflare's JWKS endpoint and trusts
the contained claims as the user identity.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.metrics import AUTH_FAILURE_TOTAL, BACKEND_ERRORS
from app.models import Role, User
from app.services.user_handle import derive_handle_from_email, next_unique_handle

logger = logging.getLogger(__name__)


_REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss"]


def redact_email(value: str | None) -> str:
    """Return a logging-safe form of an email address.

    ``alice@example.com`` -> ``a***@example.com``. The local part length
    is hidden so an attacker reading Loki can't fingerprint by local-part
    character count; the domain is preserved so operators can still
    distinguish corporate-vs-external traffic during incident triage.

    Malformed inputs (no '@', empty, None) degrade to a fixed sentinel
    string so the redacted form is never the raw input.
    """
    if value is None:
        return "<redacted-none>"
    if not value or "@" not in value:
        return "<redacted-malformed>"
    first, _, domain = value.partition("@")
    if not first:
        return "<redacted-malformed>"
    return f"{first[0]}***@{domain}"


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
    # PyJWT accepts `aud` whether it's a string or a list where our aud is
    # among several members. Cloudflare Access actually emits a single-element
    # list (`aud: ["<our-aud>"]`) — that must pass. What we want to reject is
    # a MULTI-element list, which would represent a token minted for several
    # apps in the same Cloudflare account. Narrow the allowed shapes to those
    # two unambiguous forms.
    aud = claims.get("aud")
    if aud == expected_aud:
        return claims
    if isinstance(aud, list) and len(aud) == 1 and aud[0] == expected_aud:
        return claims
    raise pyjwt.InvalidAudienceError(
        f"aud must equal {expected_aud!r} or [{expected_aud!r}], got {aud!r}"
    )


def _default_display_name_for(email: str) -> str:
    """Auto-derive a display_name for a brand-new SSO row.

    Service-token principals get a friendly fixed label — their email
    local part is a 64-char hex stamp humans can't read in Discord
    embeds or admin tables.
    """
    from app.models.user import SERVICE_TOKEN_DISPLAY_NAME, SERVICE_TOKEN_EMAIL_DOMAIN

    if email.endswith(SERVICE_TOKEN_EMAIL_DOMAIN):
        return SERVICE_TOKEN_DISPLAY_NAME
    return email.split("@", 1)[0]


async def get_or_create_user_by_email(session: AsyncSession, email: str) -> User:
    from app.models.user import SERVICE_TOKEN_DISPLAY_NAME, SERVICE_TOKEN_EMAIL_DOMAIN
    from app.services.audit import write_audit_log

    existing = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is not None:
        # A service-token row created by older code carries the raw email
        # local-part as display_name (a 64-char hex stamp). Rewrite to
        # the friendly label on next visit — but only if the current
        # value still matches the auto-derived form, so we never clobber
        # a name an admin chose deliberately.
        #
        # The rename is purely cosmetic: a transient DB error here must
        # not break the auth path. Failures are swallowed (rollback +
        # metric); the next visit retries.
        if (
            email.endswith(SERVICE_TOKEN_EMAIL_DOMAIN)
            and existing.display_name == email.split("@", 1)[0]
        ):
            existing.display_name = SERVICE_TOKEN_DISPLAY_NAME
            try:
                await session.commit()
                await session.refresh(existing)
            except SQLAlchemyError:
                BACKEND_ERRORS.labels(stage="display_name_rename").inc()
                logger.warning(
                    "service-token display_name rename failed for %s",
                    email,
                    exc_info=True,
                )
                await session.rollback()
        return existing

    # Derive a slug-safe handle for the new user.
    # Collision-resolves against the set of currently used handles.
    existing_handles = set((await session.execute(select(User.handle))).scalars().all())
    base_handle = derive_handle_from_email(email)
    handle = next_unique_handle(base_handle, existing=existing_handles)

    initial_role = (
        Role.SERVICE_TOKEN if email.endswith(SERVICE_TOKEN_EMAIL_DOMAIN) else Role.USER
    )
    user = User(
        email=email,
        role=initial_role,
        display_name=_default_display_name_for(email),
        handle=handle,
    )
    session.add(user)
    # Commit (not flush) so the INSERT survives the request-scope session
    # closing without an explicit commit. fastapi-users style dependencies
    # don't auto-commit; new users would otherwise rollback into the void
    # on first visit and never make it into /admin/users.
    try:
        await session.flush()
        # #166: audit first-time user resolution (auth.login = a new User
        # row is materialised). Logging on EVERY request would blow up the
        # audit_log table; logging only at the get-or-create cache-miss
        # gives us "this principal first appeared at <ts>" without
        # per-request noise.
        await write_audit_log(
            session,
            actor_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            before=None,
            after={
                "email": email,
                "role": initial_role.value,
                "handle": handle,
            },
        )
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


class CfAccessAuthError(Exception):
    """Raised by resolve_user_from_jwt when the JWT is missing or invalid.

    Callers should map this to an appropriate protocol-level response
    (HTTP 401 for the HTTP dep, WebSocket close code 4401 for the WS helper).
    """


async def resolve_user_from_jwt(
    session: AsyncSession,
    token: str | None,
    *,
    log_context: str = "",
) -> User:
    """Verify a Cloudflare Access JWT and return the (get-or-created) User.

    Shared between the HTTP dep (`cf_access_user`) and the WebSocket auth
    helper (`resolve_user_from_ws`). The two protocols can't share a
    FastAPI `Depends()` chain, so the shared logic lives here and each
    caller wraps the exception into its protocol's error shape.

    `log_context` is a human-readable hint ("path=/foo" or "ws=/jobs/…") for
    the warning line emitted on auth failure.
    """
    if settings.AUTH_DEV_MODE:
        if not settings.AUTH_DEV_EMAIL:
            raise CfAccessAuthError("AUTH_DEV_MODE enabled but AUTH_DEV_EMAIL empty")
        return await get_or_create_user_by_email(session, settings.AUTH_DEV_EMAIL)

    if not token:
        logger.warning(
            "cf_access 401 %s: missing Cf-Access-Jwt-Assertion",
            log_context,
        )
        AUTH_FAILURE_TOTAL.labels(reason="missing_header").inc()
        raise CfAccessAuthError("missing Cf-Access-Jwt-Assertion header")

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
    except pyjwt.PyJWKClientError as e:
        logger.warning("cf_access 401 %s: JWKS lookup failed: %s", log_context, e)
        AUTH_FAILURE_TOTAL.labels(reason="jwks_lookup_failed").inc()
        raise CfAccessAuthError(f"jwks lookup failed: {e}") from e
    except pyjwt.InvalidTokenError as e:
        # PyJWT's get_signing_key_from_jwt parses the JWT header internally
        # to extract `kid` before any signature work — a token with the wrong
        # shape (no segments, junk b64) raises DecodeError (subclass of
        # InvalidTokenError) HERE, before reaching verify_cf_token below.
        # Without this catch, malformed tokens bubble up as a 500.
        # Folded into the broad invalid_signature bucket — distinguishing
        # "syntactically broken" from "signature mismatch" is debugging
        # detail, not actionable for the operator-level alert.
        logger.warning("cf_access 401 %s: malformed JWT: %s", log_context, e)
        AUTH_FAILURE_TOTAL.labels(reason="invalid_signature").inc()
        raise CfAccessAuthError(f"invalid Cloudflare Access token: {e}") from e

    try:
        claims = verify_cf_token(
            token=token,
            signing_key=signing_key,
            expected_aud=settings.CF_ACCESS_APP_AUD,
            expected_iss=f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
        )
    except pyjwt.InvalidTokenError as e:
        try:
            unverified = pyjwt.decode(token, options={"verify_signature": False})
            peek = {
                "aud": unverified.get("aud"),
                "iss": unverified.get("iss"),
                "email": redact_email(unverified.get("email")),
                "exp": unverified.get("exp"),
            }
        except Exception:
            peek = "unparseable"  # type: ignore[assignment]  # fallback string for error logging
        logger.warning(
            "cf_access 401 %s: JWT invalid: %s. expected_aud=%s expected_iss=%s claims_peek=%s",
            log_context,
            e,
            settings.CF_ACCESS_APP_AUD,
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}",
            peek,
        )
        AUTH_FAILURE_TOTAL.labels(reason="invalid_signature").inc()
        raise CfAccessAuthError(f"invalid Cloudflare Access token: {e}") from e

    # User SSO JWTs carry `email`. Service-token JWTs carry `common_name`
    # (the service-token name) and no email — synthesize a stable identifier
    # so the same User row is reused across calls.
    email = claims.get("email")
    if not email:
        common_name = claims.get("common_name")
        if not common_name:
            logger.warning(
                "cf_access 401 %s: JWT has neither email nor common_name claim",
                log_context,
            )
            AUTH_FAILURE_TOTAL.labels(reason="missing_principal_claim").inc()
            raise CfAccessAuthError("token has neither email nor common_name claim")
        email = f"service-{common_name}@cf-access.local"
    return await get_or_create_user_by_email(session, email)


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
    token = request.headers.get("cf-access-jwt-assertion")
    if token is None and not settings.AUTH_DEV_MODE:
        # Preserve the pre-refactor warning line that enumerates cf-* headers
        # so operators can see whether the CF IAP actually attached the JWT.
        cf_hdrs = sorted(k for k in request.headers if k.lower().startswith("cf-"))
        logger.warning(
            "cf_access_user 401 path=%s: missing Cf-Access-Jwt-Assertion. cf-* headers present: %s",
            request.url.path,
            cf_hdrs,
        )
    try:
        return await resolve_user_from_jwt(
            session, token, log_context=f"path={request.url.path}"
        )
    except CfAccessAuthError as e:
        raise HTTPException(401, str(e)) from e
