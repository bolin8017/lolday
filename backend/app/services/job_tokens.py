"""Job-scoped one-time tokens.

Used by init containers to authenticate back to the backend for config/CSV fetch.
Raw token lives in a K8s Secret injected into the init container; the backend
stores only the SHA256 hash in the DB. Secret is deleted on job finalize.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token() -> str:
    """Return URL-safe base64 token of 32 random bytes (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA256 hex digest of the token bytes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, stored_hash: str) -> bool:
    """Constant-time comparison of hash(token) against stored_hash."""
    return hmac.compare_digest(hash_token(token), stored_hash)
