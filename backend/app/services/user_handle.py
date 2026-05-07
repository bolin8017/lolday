"""Slug derivation rules for `User.handle`.

Mirrors HuggingFace / GitHub conventions: lowercase alphanumeric +
`_` + `-`, must start with a letter, no trailing `-`, no consecutive
`--`, length 1..60. The migration (one-shot) and cf_access (per-login)
both call ``derive_handle_from_email`` and resolve collisions via
``next_unique_handle``.
"""

from __future__ import annotations

import re
import uuid

HANDLE_MAX_LEN = 60
_VALID_RE = re.compile(r"^[a-z][a-z0-9_-]*[a-z0-9]$|^[a-z]$")


def is_valid_handle(handle: str) -> bool:
    if not handle or len(handle) > HANDLE_MAX_LEN:
        return False
    if "--" in handle:
        return False
    return bool(_VALID_RE.fullmatch(handle))


def _slugify(raw: str) -> str:
    s = raw.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s


def derive_handle_from_email(email: str) -> str:
    """Slug-safe derivation; resolution to uniqueness is caller's job."""
    local = email.split("@", 1)[0]
    handle = _slugify(local)

    if not handle:
        # CF-Access service token / weird email: fall back to UUID short form
        handle = "u-" + uuid.uuid4().hex[:8]
    elif handle[0].isdigit():
        handle = "u-" + handle

    if len(handle) > HANDLE_MAX_LEN:
        handle = handle[:HANDLE_MAX_LEN].rstrip("-")

    return handle


def next_unique_handle(base: str, *, existing: set[str]) -> str:
    """Return ``base`` if unused, else ``base-2``, ``base-3``, ... — first unused."""
    if base not in existing:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        room = HANDLE_MAX_LEN - len(suffix)
        candidate = base[:room].rstrip("-") + suffix
        if candidate not in existing:
            return candidate
        n += 1
