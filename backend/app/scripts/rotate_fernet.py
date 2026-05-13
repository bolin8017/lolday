"""Re-encrypt UserGitCredential.encrypted_token from OLD Fernet key to NEW.

Usage::

    cd backend
    uv run python -m app.scripts.rotate_fernet --old "$OLD_KEY" --new "$NEW_KEY"

Idempotent: rows already decryptable under NEW alone are skipped. Aborts on
the first row that decrypts under neither key, leaving already-rotated rows
intact for inspection / re-run. Run BEFORE retiring the OLD key from
FERNET_KEYS. See docs/runbooks/p3-fernet-rotation.md for the full operator
procedure.

Why explicit --old / --new CLI args (not env): the running backend's
FERNET_KEYS env reflects whatever was deployed; the rotation script needs
to know which key was the previous active encrypt key and which is the new
one, independent of the deployment state at run time.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Any

from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.models.credential import UserGitCredential
from app.services.crypto import TokenCipher

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

# Module-level symbol kept overrideable by tests via monkeypatch (the standard
# attribute-injection pattern). Lazily populated from ``app.db`` on first call
# so ``python -m app.scripts.rotate_fernet --help`` does not trigger the
# production-mode ``Settings()`` validators at module import (which would
# refuse an empty CF_ACCESS_TEAM_DOMAIN in a fresh dev shell — irrelevant to
# argparse-only invocations).
async_session_maker: async_sessionmaker[Any] | None = None

logger = logging.getLogger(__name__)


async def rotate_all(old_key: str, new_key: str) -> tuple[int, int]:
    """Re-encrypt every UserGitCredential row. Returns (rotated, skipped).

    Per-row commit is the autonomous-transaction primitive: under PostgreSQL,
    each ``await session.commit()`` is its own transaction. Aborting after a
    partial run leaves committed rows in the NEW-key state and unprocessed
    rows in the OLD-key state — both are decryptable on the running backend
    under ``MultiFernet([NEW, OLD])``.

    Raises ``RuntimeError`` on the first row that decrypts under neither key.
    """
    global async_session_maker
    if async_session_maker is None:
        # Lazy import — keeps ``--help`` free of production Settings validation.
        from app.db import async_session_maker as _maker

        async_session_maker = _maker
    new_cipher = TokenCipher(new_key)
    old_cipher = TokenCipher(old_key)
    rotated, skipped = 0, 0
    async with async_session_maker() as session:
        rows = (await session.execute(select(UserGitCredential))).scalars().all()
        for row in rows:
            # Already-rotated? Skip silently — re-runs are safe.
            try:
                new_cipher.decrypt(row.encrypted_token)
                skipped += 1
                continue
            except InvalidToken:
                pass
            # Decrypt under OLD; abort if it fails.
            try:
                plaintext = old_cipher.decrypt(row.encrypted_token)
            except InvalidToken as exc:
                logger.error(
                    "rotate_fernet: row user_id=%s decrypts under neither old "
                    "nor new key; aborting (committed rows are already in "
                    "NEW state)",
                    row.user_id,
                )
                raise RuntimeError(f"unrotatable row: user_id={row.user_id}") from exc
            # Re-encrypt with NEW and commit (per-row autonomous tx).
            row.encrypted_token = new_cipher.encrypt(plaintext)
            await session.commit()
            rotated += 1
    return rotated, skipped


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s rotate_fernet: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description=(
            "Re-encrypt UserGitCredential rows from OLD to NEW Fernet key. "
            "Run during the maintenance window described in "
            "docs/runbooks/p3-fernet-rotation.md."
        )
    )
    parser.add_argument("--old", required=True, help="base64 Fernet key being retired")
    parser.add_argument(
        "--new",
        required=True,
        help="base64 Fernet key already deployed as the active encrypt key",
    )
    args = parser.parse_args()
    try:
        rotated, skipped = asyncio.run(rotate_all(args.old, args.new))
    except RuntimeError as exc:
        logger.error("rotate_fernet aborted: %s", exc)
        return 2
    logger.info("done — rotated=%d skipped=%d", rotated, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
