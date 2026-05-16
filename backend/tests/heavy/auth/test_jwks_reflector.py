"""§10 #30 carryover — D2.4 Task 13 (JWKS reflector heavy).

Spec: docs/superpowers/specs/2026-05-15-test-architecture-redesign-design.md §10 #30.
Predecessor: backend/tests/integration/services/test_jwks_cache_ttl.py
asserts the PyJWKClient instance is constructed with the right
cache_jwk_set / lifespan / lru_cache arguments (structural). This
module locks the _behavioural_ side: actually serve a JWKS at
/.well-known/jwks.json from a uvicorn process, mint an RSA key,
sign a JWT with that key, hand it to PyJWKClient, and verify:

1. The signed JWT verifies end-to-end against the reflector.
2. Back-to-back PyJWKClient calls share the cache (one fetch).
3. After explicit cache invalidation (cache.put(None) — mirrors what
   happens when the JWKS endpoint's response changes mid-flight),
   the next call re-fetches.

freezegun is NOT used here because PyJWT's JWKSetCache uses
``time.monotonic()`` for expiry — freezegun's ``tick(delta=N)`` jumps
``time.monotonic()`` by far more than N (to the wall-clock value),
which makes the cache appear expired immediately. The behaviour
under test (cache holds within TTL; invalidation triggers re-fetch)
is still locked via the in-process cache poke in test 3.

uvicorn-as-test-fixture pattern: start uvicorn in a thread with
Config(loop="asyncio", log_level="warning") against a tiny
Starlette app, poll until the port responds, run the test body,
shut down cleanly via Server.should_exit.

Marked heavy → runs in backend-slow.yml on main push + nightly.
Unique among heavy tests: no testcontainers / Docker dependency.
"""

from __future__ import annotations

import base64
import contextlib
import socket
import threading
import time

import httpx
import jwt as pyjwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

pytestmark = [pytest.mark.heavy]


def _mint_rsa() -> tuple[rsa.RSAPrivateKey, dict]:
    """Generate an RSA-2048 key + a JWKS-shaped public JWK dict."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def _b64url(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n),
        "e": _b64url(numbers.e),
    }
    return key, jwk


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ReflectorServer:
    """Spin uvicorn in a background thread serving /.well-known/jwks.json."""

    def __init__(self) -> None:
        self.key, jwk = _mint_rsa()
        self.jwk = jwk
        self.jwks_fetch_count = 0
        self.port = _free_port()
        self.thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None
        self._lock = threading.Lock()

        async def jwks(request):
            with self._lock:
                self.jwks_fetch_count += 1
            return JSONResponse({"keys": [jwk]})

        self.app = Starlette(routes=[Route("/.well-known/jwks.json", jwks)])

    def start(self) -> None:
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                resp = httpx.get(self.jwks_url, timeout=1.0)
                if resp.status_code == 200:
                    # The readiness probe itself bumps jwks_fetch_count.
                    # Reset so tests count only the PyJWKClient-driven fetches.
                    with self._lock:
                        self.jwks_fetch_count = 0
                    return
            time.sleep(0.05)
        raise RuntimeError("reflector server failed to start")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)

    @property
    def jwks_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/.well-known/jwks.json"

    def sign(self, payload: dict) -> str:
        """Sign a JWT with the reflector's RSA key."""
        pem = self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pyjwt.encode(
            payload, pem, algorithm="RS256", headers={"kid": self.jwk["kid"]}
        )


@pytest.fixture
def reflector():
    s = _ReflectorServer()
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_jwks_client_verifies_signed_jwt_against_reflector(
    reflector: _ReflectorServer,
) -> None:
    """End-to-end: PyJWKClient fetches the reflector's JWKS and verifies a JWT."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    token = reflector.sign({"sub": "u@example.com", "aud": "test-aud"})
    signing_key = client.get_signing_key_from_jwt(token).key
    claims = pyjwt.decode(token, signing_key, algorithms=["RS256"], audience="test-aud")
    assert claims["sub"] == "u@example.com"
    assert reflector.jwks_fetch_count == 1


def test_jwks_client_cache_holds_back_to_back(reflector: _ReflectorServer) -> None:
    """Back-to-back PyJWKClient calls share the cache — one fetch total."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    token1 = reflector.sign({"sub": "u1"})
    token2 = reflector.sign({"sub": "u2"})
    client.get_signing_key_from_jwt(token1)
    client.get_signing_key_from_jwt(token2)
    assert reflector.jwks_fetch_count == 1, (
        f"JWKS cache leaked: expected 1 fetch on back-to-back calls, "
        f"got {reflector.jwks_fetch_count}"
    )


def test_jwks_client_refreshes_after_explicit_invalidation(
    reflector: _ReflectorServer,
) -> None:
    """Invalidating the cache (cache.put(None)) forces the next call
    to re-fetch from the endpoint. Mirrors the real-world recovery path
    when the JWKS endpoint rotates keys mid-flight."""
    client = pyjwt.PyJWKClient(reflector.jwks_url, cache_jwk_set=True, lifespan=600)
    client.get_signing_key_from_jwt(reflector.sign({"sub": "u1"}))
    assert reflector.jwks_fetch_count == 1
    assert client.jwk_set_cache is not None
    client.jwk_set_cache.put(None)  # invalidate
    client.get_signing_key_from_jwt(reflector.sign({"sub": "u2"}))
    assert reflector.jwks_fetch_count == 2, (
        f"JWKS cache did not refresh after invalidation; "
        f"expected 2 fetches, got {reflector.jwks_fetch_count}"
    )
