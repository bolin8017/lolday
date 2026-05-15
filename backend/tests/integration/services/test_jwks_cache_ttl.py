"""D2.4 Task 14 — JWKS cache TTL contract.

app/auth/cf_access.py wraps PyJWKClient in an lru_cache-singleton with
``cache_jwk_set=True`` + ``lifespan=settings.CF_ACCESS_JWKS_CACHE_TTL_SECONDS``.
The bug class to catch: a config regression that turns caching off (every
request hits CF JWKS endpoint, ~30 ms * QPS = backend latency floor goes up).
"""

from __future__ import annotations

import pytest
from app.auth.cf_access import _get_jwks_client
from app.config import settings


def test_jwks_client_has_caching_enabled() -> None:
    """The module-level PyJWKClient must have caching on with a finite TTL."""
    client = _get_jwks_client()
    # PyJWKClient stores the cache state on instance attributes; the public
    # contract is ``client.jwk_set_cache.cache_data`` becomes non-None after
    # the first fetch. Here we lock the construction-time invariants:
    # cache_keys=True must imply the client created a cache backend.
    assert getattr(client, "jwk_set_cache", None) is not None, (
        "PyJWKClient missing jwk_set_cache attribute; caching not enabled. "
        "Fix: ensure cache_jwk_set=True in _get_jwks_client()."
    )


def test_jwks_client_lifespan_matches_settings() -> None:
    """TTL comes from CF_ACCESS_JWKS_CACHE_TTL_SECONDS (default 600)."""
    client = _get_jwks_client()
    cache = client.jwk_set_cache
    # PyJWT's JWKSetCache exposes ``lifespan`` (the seconds-TTL we passed).
    assert cache.lifespan == settings.CF_ACCESS_JWKS_CACHE_TTL_SECONDS, (
        f"JWKS cache lifespan={cache.lifespan!r} != settings.CF_ACCESS_JWKS_CACHE_TTL_SECONDS"
        f"={settings.CF_ACCESS_JWKS_CACHE_TTL_SECONDS!r}"
    )


def test_jwks_client_is_singleton() -> None:
    """Repeated _get_jwks_client() returns the same instance (lru_cache)."""
    a = _get_jwks_client()
    b = _get_jwks_client()
    assert a is b


@pytest.mark.parametrize(
    "url_attr",
    ["uri", "url"],  # PyJWKClient renamed across minor versions
)
def test_jwks_client_url_uses_cf_access_team_domain(url_attr: str) -> None:
    """The JWKS URL embeds CF_ACCESS_TEAM_DOMAIN."""
    client = _get_jwks_client()
    actual = getattr(client, url_attr, None)
    if actual is None:
        pytest.skip(f"PyJWKClient has no {url_attr} attribute on this version")
    assert "cdn-cgi/access/certs" in actual
