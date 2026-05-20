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


def test_verify_cf_token_rejects_wrong_aud(rsa_keypair):
    """Cross-app token reuse guard: aud claim must equal our app's aud."""
    from app.auth.cf_access import verify_cf_token

    token = _sign(rsa_keypair, _valid_claims() | {"aud": "some-other-app-uid"})
    with pytest.raises(pyjwt.InvalidAudienceError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


def test_verify_cf_token_accepts_single_element_list_aud(rsa_keypair):
    """Cloudflare Access actually emits `aud` as a single-element list
    (`['<our-aud>']`), NOT a bare string. We must accept that shape — the
    previous iteration of this check rejected it and locked everyone out
    of production."""
    from app.auth.cf_access import verify_cf_token

    token = _sign(rsa_keypair, _valid_claims() | {"aud": ["test-app-uid"]})
    claims = verify_cf_token(
        token=token,
        signing_key=rsa_keypair.public_key(),
        expected_aud="test-app-uid",
        expected_iss="https://test.cloudflareaccess.com",
    )
    assert claims["email"] == "alice@example.com"


def test_verify_cf_token_rejects_multi_element_list_aud(rsa_keypair):
    """A multi-aud token (one signed for several apps) would let a token
    minted for app A authenticate here if our aud is also in the list.
    Reject that shape even though PyJWT's list-membership rule accepts it."""
    from app.auth.cf_access import verify_cf_token

    token = _sign(
        rsa_keypair,
        _valid_claims() | {"aud": ["test-app-uid", "some-other-app-uid"]},
    )
    with pytest.raises(pyjwt.InvalidAudienceError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


def test_verify_cf_token_rejects_wrong_iss(rsa_keypair):
    """Cross-team token reuse guard: iss must be our team domain."""
    from app.auth.cf_access import verify_cf_token

    token = _sign(
        rsa_keypair,
        _valid_claims() | {"iss": "https://attacker.cloudflareaccess.com"},
    )
    with pytest.raises(pyjwt.InvalidIssuerError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


def test_verify_cf_token_rejects_expired_token(rsa_keypair):
    from app.auth.cf_access import verify_cf_token

    now = int(time.time())
    token = _sign(
        rsa_keypair,
        _valid_claims() | {"iat": now - 600, "exp": now - 10},
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


def test_verify_cf_token_rejects_token_signed_by_different_key(rsa_keypair):
    """Pins that verify_cf_token really passes the key through — a future
    refactor that accidentally disables signature verification would regress."""
    from app.auth.cf_access import verify_cf_token
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    attacker_priv = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    token = _sign(attacker_priv, _valid_claims())

    with pytest.raises(pyjwt.InvalidSignatureError):
        verify_cf_token(
            token=token,
            signing_key=rsa_keypair.public_key(),  # victim's key, not attacker's
            expected_aud="test-app-uid",
            expected_iss="https://test.cloudflareaccess.com",
        )


async def test_get_or_create_user_creates_new_row_with_defaults(db_session):
    """First visit by a new email auto-provisions a User with role=USER,
    display_name and handle derived from email local-part."""
    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import Role, User
    from sqlalchemy import select

    user = await get_or_create_user_by_email(db_session, "newbie@example.com")

    assert user.email == "newbie@example.com"
    assert user.role == Role.USER
    assert user.display_name == "newbie"
    assert user.handle == "newbie"

    row = (
        await db_session.execute(select(User).where(User.email == "newbie@example.com"))
    ).scalar_one()
    assert row.id == user.id


async def test_get_or_create_user_persists_across_sessions(db_session):
    """Real bug: `session.flush()` without commit would make the row visible
    in the same session (passing naive same-session tests) but vanish when
    the request-scoped session closed in production, because `async with
    async_session_maker() as s:` does NOT auto-commit on context exit."""
    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import User
    from sqlalchemy import select

    from tests.conftest import test_session_maker

    await get_or_create_user_by_email(db_session, "persists@example.com")
    await db_session.close()  # simulate request-scope session termination

    async with test_session_maker() as fresh:
        row = (
            await fresh.execute(
                select(User).where(User.email == "persists@example.com")
            )
        ).scalar_one_or_none()
    assert row is not None, "row lost on session close — get_or_create forgot to commit"


async def test_get_or_create_user_returns_existing_row(db_session):
    """Subsequent visits re-use the existing User row without creating duplicates."""
    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import User
    from sqlalchemy import func, select

    a = await get_or_create_user_by_email(db_session, "returning@example.com")
    b = await get_or_create_user_by_email(db_session, "returning@example.com")

    assert a.id == b.id
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(User)
            .where(User.email == "returning@example.com")
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

    token = _sign(
        rsa_keypair, _valid_claims() | {"iss": "https://test.cloudflareaccess.com"}
    )
    req = _make_request(headers=[(b"cf-access-jwt-assertion", token.encode())])

    user = await cf.cf_access_user(request=req, session=db_session)

    assert user.email == "alice@example.com"
    assert user.role.value == "user"


async def test_cf_access_user_raises_401_when_header_missing(db_session, monkeypatch):
    from app.auth.cf_access import cf_access_user
    from app.config import settings
    from fastapi import HTTPException

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


async def test_auth_failure_total_increments_on_jwks_lookup_failure(monkeypatch):
    """A JWKS-server outage (DNS error, 5xx, certificate expiry) surfaces as
    `pyjwt.PyJWKClientError` out of `get_signing_key_from_jwt`. Must
    increment `AUTH_FAILURE_TOTAL{reason="jwks_lookup_failed"}` so the
    `LoldayBackendErrorRateElevated` alert pages the operator rather
    than silently 500-ing every request.

    Covers lines 256-258 in `app/auth/cf_access.py::resolve_user_from_jwt`.
    Distinct from the existing `invalid_signature` test which bypasses the
    JWKS lookup with a FakeJwksClient that succeeds and only the verify
    step fails."""
    import jwt as pyjwt
    from app.auth import cf_access
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    class _FailingJwksClient:
        def get_signing_key_from_jwt(self, _token):
            raise pyjwt.PyJWKClientError("simulated JWKS endpoint unreachable")

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _FailingJwksClient())

    before = _read_counter("lolday_auth_failure_total", reason="jwks_lookup_failed")

    with pytest.raises(CfAccessAuthError, match="jwks lookup failed"):
        await resolve_user_from_jwt(
            session=None, token="any-token", log_context="jwks-outage"
        )

    after = _read_counter("lolday_auth_failure_total", reason="jwks_lookup_failed")
    assert after - before == pytest.approx(1.0)


async def test_resolve_user_dev_mode_with_empty_email_raises(db_session, monkeypatch):
    """`AUTH_DEV_MODE=true` + empty `AUTH_DEV_EMAIL` is a misconfiguration —
    must raise `CfAccessAuthError` rather than silently fall through to a
    JWT-validation path that would never succeed.

    Covers the defensive guard at `resolve_user_from_jwt` for the
    config-error case. The production boot-time `validate_sso_config`
    catches `AUTH_DEV_MODE=true` in production environments, but this
    path defends against a partially-broken dev override (`AUTH_DEV_MODE`
    flipped without `AUTH_DEV_EMAIL`)."""
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", True)
    monkeypatch.setattr(settings, "AUTH_DEV_EMAIL", "")
    # AUTH_DEV_PERSONAS empty → bypass the persona-routing branch and hit
    # the AUTH_DEV_EMAIL check that has no persona to fall back to.
    monkeypatch.setattr(settings, "AUTH_DEV_PERSONAS", {})

    with pytest.raises(CfAccessAuthError, match="AUTH_DEV_EMAIL"):
        await resolve_user_from_jwt(
            session=db_session, token=None, log_context="dev-mode-misconfig"
        )


async def test_claims_peek_succeeds_on_validly_shaped_but_wrong_signed_token(
    monkeypatch, rsa_keypair, caplog
):
    """A JWT signed by an attacker's key but otherwise well-formed: the
    signature verification fails BUT `pyjwt.decode(..., verify_signature=False)`
    parses successfully, so the claims-peek branch of the warning-log path
    populates a structured `peek` dict (not the `"unparseable"` fallback).

    Distinct from the existing claims-peek test which passes the literal
    string `"not-a-real-jwt"` — that hits the `except Exception` arm of
    the peek try-block. This one exercises the dict-construction arm
    (lines 282-287), the only path that produces actionable structured
    information in the operator-facing warning log.
    """
    import io
    import logging

    from app.auth import cf_access
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    # Attacker signs a perfectly-shaped JWT but with a key that doesn't
    # match the JWKS-published key. JWT decode passes; signature check
    # fails on verify_cf_token.
    attacker_priv = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    attacker_token = _sign(attacker_priv, _valid_claims())

    class _VictimJwksClient:
        def get_signing_key_from_jwt(self, _token):
            class _K:
                key = rsa_keypair.public_key()  # the WRONG key for this token

            return _K()

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _VictimJwksClient())

    # Logger handler capture (same pattern as test_claims_peek_redacts_email
    # — avoids the alembic `disable_existing_loggers` interaction).
    logger_obj = logging.getLogger("app.auth.cf_access")
    saved_disabled = logger_obj.disabled
    logger_obj.disabled = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    logger_obj.addHandler(handler)
    try:
        with pytest.raises(CfAccessAuthError):
            await resolve_user_from_jwt(
                session=None, token=attacker_token, log_context="peek-test"
            )
    finally:
        logger_obj.removeHandler(handler)
        logger_obj.disabled = saved_disabled

    output = buf.getvalue()
    # Structured peek landed (not the "unparseable" fallback).
    assert "unparseable" not in output
    assert "test-app-uid" in output  # aud from the well-formed token's claims
    assert "a***@example.com" in output  # email was redacted into the peek


async def test_resolve_user_rejects_jwt_without_email_or_common_name(
    db_session, monkeypatch, rsa_keypair
):
    """A JWT that passes signature verification but lacks BOTH `email` AND
    `common_name` must be rejected. SSO JWTs carry `email`; service-token
    JWTs carry `common_name`. A token with neither has no stable principal
    to materialise a User row against and must surface as
    `AUTH_FAILURE_TOTAL{reason="missing_principal_claim"}`.

    Covers lines 306-314 in `resolve_user_from_jwt`. Distinct from
    `invalid_signature` (signature mismatch, claims never inspected) and
    `missing_header` (no token at all)."""
    from app.auth import cf_access
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    # JWKS client returns the real public key so signature verification
    # passes; the claims-shape failure happens AFTER verify_cf_token returns.
    class _RealJwksClient:
        def get_signing_key_from_jwt(self, _token):
            class _K:
                key = rsa_keypair.public_key()

            return _K()

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _RealJwksClient())

    # Valid claims minus email + common_name (sub alone is not enough —
    # the lolday code path needs one of the two named principal claims).
    claims = _valid_claims()
    del claims["email"]
    token = _sign(rsa_keypair, claims)

    before = _read_counter(
        "lolday_auth_failure_total", reason="missing_principal_claim"
    )

    with pytest.raises(CfAccessAuthError, match="neither email nor common_name"):
        await resolve_user_from_jwt(
            session=db_session, token=token, log_context="missing-principal"
        )

    after = _read_counter("lolday_auth_failure_total", reason="missing_principal_claim")
    assert after - before == pytest.approx(1.0)


async def test_resolve_user_accepts_service_token_jwt_with_common_name(
    db_session, monkeypatch, rsa_keypair
):
    """A service-token JWT has no `email` but carries `common_name`. The
    code synthesizes `service-<common_name>@cf-access.local` and proceeds
    with normal user provisioning. Pins the happy-path branch sibling of
    the missing-principal test."""
    from app.auth import cf_access
    from app.auth.cf_access import resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    class _RealJwksClient:
        def get_signing_key_from_jwt(self, _token):
            class _K:
                key = rsa_keypair.public_key()

            return _K()

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _RealJwksClient())

    # Service-token shape: no email, common_name set.
    claims = _valid_claims()
    del claims["email"]
    claims["common_name"] = "ci-bot"
    token = _sign(rsa_keypair, claims)

    user = await resolve_user_from_jwt(
        session=db_session, token=token, log_context="service-token-path"
    )
    assert user.email == "service-ci-bot@cf-access.local"


async def test_first_login_derives_handle(db_session):
    """New user gets a handle derived from their email prefix."""
    from app.auth.cf_access import get_or_create_user_by_email

    user = await get_or_create_user_by_email(db_session, "newuser@example.com")

    assert user.email == "newuser@example.com"
    assert user.handle == "newuser"


async def test_handle_collision_appends_suffix(db_session):
    """When the derived handle collides, a -N suffix is appended."""
    from app.auth.cf_access import get_or_create_user_by_email
    from app.models import Role, User

    # Pre-create a user occupying "alice"
    db_session.add(
        User(
            email="alice@first.com",
            handle="alice",
            role=Role.USER,
            display_name="Alice First",
        )
    )
    await db_session.commit()

    # Second user with the same email prefix logs in
    user = await get_or_create_user_by_email(db_session, "alice@second.com")

    assert user.email == "alice@second.com"
    assert user.handle == "alice-2"


# H-27 (security-hardening P5) — AUTH_FAILURE_TOTAL counter checks. The
# counter is global to the prometheus_client default REGISTRY; tests use the
# diff-by-N pattern (read before, do action, read after, assert delta) to
# stay robust against other tests touching the same metric in the same run.
def _read_counter(metric_name: str, **labels: str) -> float:
    """Read a labeled Counter's current value from the default REGISTRY."""
    from prometheus_client import REGISTRY

    value = REGISTRY.get_sample_value(metric_name, labels=labels)
    return 0.0 if value is None else value


async def test_auth_failure_total_increments_on_invalid_signature(monkeypatch):
    """A JWT with a bad signature must increment AUTH_FAILURE_TOTAL{reason='invalid_signature'}."""
    from app.auth import cf_access
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            class _K:
                key = b"unrelated-public-key-bytes"

            return _K()

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _FakeJwksClient())

    before = _read_counter("lolday_auth_failure_total", reason="invalid_signature")

    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt

    with pytest.raises(CfAccessAuthError):
        await resolve_user_from_jwt(
            session=None, token="not-a-real-jwt", log_context="test"
        )

    after = _read_counter("lolday_auth_failure_total", reason="invalid_signature")
    assert after - before == pytest.approx(1.0)


async def test_auth_failure_total_increments_on_missing_header(monkeypatch):
    """A None token must increment AUTH_FAILURE_TOTAL{reason='missing_header'}."""
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)

    before = _read_counter("lolday_auth_failure_total", reason="missing_header")

    with pytest.raises(CfAccessAuthError):
        await resolve_user_from_jwt(session=None, token=None, log_context="test")

    after = _read_counter("lolday_auth_failure_total", reason="missing_header")
    assert after - before == pytest.approx(1.0)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("alice@example.com", "a***@example.com"),
        ("b@example.com", "b***@example.com"),
        ("verylonglocalpart@subdomain.example.org", "v***@subdomain.example.org"),
        # malformed inputs degrade safely, never raise
        ("no-at-sign", "<redacted-malformed>"),
        ("", "<redacted-malformed>"),
        # Empty local-part (`@domain`) — distinct from "no @ sign":
        # the partition succeeds but `first` is empty.
        ("@example.com", "<redacted-malformed>"),
        (None, "<redacted-none>"),
    ],
)
def test_redact_email(raw, expected):
    from app.auth.cf_access import redact_email

    assert redact_email(raw) == expected


async def test_claims_peek_redacts_email(monkeypatch):
    """The claims_peek warning log line must not contain a raw email after T6 lands.

    Direct logger-capture pattern (NOT pytest's caplog) — avoids the
    alembic ``disable_existing_loggers`` interaction documented in
    auto-memory ``project_caplog_alembic_logger_disabled.md``.
    """
    import io
    import logging

    from app.auth import cf_access
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            class _K:
                key = b"unrelated"

            return _K()

    monkeypatch.setattr(cf_access, "_get_jwks_client", lambda: _FakeJwksClient())

    logger = logging.getLogger("app.auth.cf_access")
    saved_disabled = logger.disabled
    logger.disabled = False
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        with pytest.raises(CfAccessAuthError):
            await resolve_user_from_jwt(
                session=None, token="not-a-real-jwt", log_context="test"
            )
    finally:
        logger.removeHandler(handler)
        logger.disabled = saved_disabled

    log_text = buf.getvalue()
    assert "alice@example.com" not in log_text
    # The bad token can't be decoded, so the peek dict becomes "unparseable".
    # The redaction itself is independently verified by test_redact_email above;
    # this test pins down that the WARNING line never carries a raw email.


async def test_auth_failure_total_increments_on_malformed_jwt_shape(monkeypatch):
    """A JWT with the wrong shape (no segments) must not bubble up as 500.

    P5 follow-up: PyJWT's ``get_signing_key_from_jwt`` calls
    ``decode_complete`` to read the header BEFORE any signature work, so
    a malformed token raises ``pyjwt.DecodeError`` (subclass of
    ``InvalidTokenError``) inside the JWKS-fetch block. Without an
    explicit ``except pyjwt.InvalidTokenError`` clause there, the
    exception bubbles up to FastAPI's default handler as a 500 and the
    auth-failure counter never increments.
    """
    from app.auth.cf_access import CfAccessAuthError, resolve_user_from_jwt
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_DEV_MODE", False)
    monkeypatch.setattr(settings, "CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setattr(settings, "CF_ACCESS_APP_AUD", "test-app-uid")

    before = _read_counter("lolday_auth_failure_total", reason="invalid_signature")

    with pytest.raises(CfAccessAuthError):
        await resolve_user_from_jwt(session=None, token="not-a-jwt", log_context="test")

    after = _read_counter("lolday_auth_failure_total", reason="invalid_signature")
    assert after - before == pytest.approx(1.0)
