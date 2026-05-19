"""Tests for backend/app/scripts/rotate_fernet.py."""

import logging
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select


@pytest.mark.asyncio
async def test_rotate_reencrypts_rows_under_new_key(db_session, monkeypatch):
    """Insert a row encrypted under k1; rotate(k1, k2); row must now decrypt
    under k2 alone and fail under k1 alone."""
    from app.models import Role, User
    from app.models.credential import GitProvider, UserGitCredential
    from app.scripts import rotate_fernet
    from app.services.crypto import TokenCipher

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()

    user = User(
        email="rotate-1@x.com",
        handle="rotate-1",
        role=Role.USER,
        display_name="rotate-1",
    )
    db_session.add(user)
    await db_session.flush()
    plaintext = "ghp_a" * 8
    db_session.add(
        UserGitCredential(
            user_id=user.id,
            provider=GitProvider.GITHUB,
            encrypted_token=TokenCipher(k1).encrypt(plaintext),
            token_hint=TokenCipher.token_hint(plaintext),
        )
    )
    await db_session.commit()

    # Point rotate_fernet at the test sqlite session_maker.
    from tests.conftest import test_session_maker  # cross-test reuse

    monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

    rotated, skipped = await rotate_fernet.rotate_all(k1, k2)
    assert rotated == 1
    assert skipped == 0

    row = (
        await db_session.execute(
            select(UserGitCredential).where(UserGitCredential.user_id == user.id)
        )
    ).scalar_one()
    # Decryptable under k2 alone.
    assert TokenCipher(k2).decrypt(row.encrypted_token) == plaintext
    # NOT decryptable under k1 alone.
    with pytest.raises(InvalidToken):
        TokenCipher(k1).decrypt(row.encrypted_token)


@pytest.mark.asyncio
async def test_rotate_is_idempotent_skips_already_rotated(db_session, monkeypatch):
    """Running rotate(k1, k2) twice in a row leaves row state unchanged on
    the second run — already-decryptable-under-k2 rows are skipped."""
    from app.models import Role, User
    from app.models.credential import GitProvider, UserGitCredential
    from app.scripts import rotate_fernet
    from app.services.crypto import TokenCipher

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()

    user = User(
        email="rotate-2@x.com",
        handle="rotate-2",
        role=Role.USER,
        display_name="rotate-2",
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        UserGitCredential(
            user_id=user.id,
            provider=GitProvider.GITHUB,
            encrypted_token=TokenCipher(k1).encrypt("hello"),
            token_hint="he...lo",
        )
    )
    await db_session.commit()

    from tests.conftest import test_session_maker

    monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

    rotated1, skipped1 = await rotate_fernet.rotate_all(k1, k2)
    rotated2, skipped2 = await rotate_fernet.rotate_all(k1, k2)
    assert (rotated1, skipped1) == (1, 0)
    assert (rotated2, skipped2) == (0, 1)


@pytest.mark.asyncio
async def test_rotate_aborts_on_undecryptable_row(db_session, monkeypatch):
    """A row encrypted under a third unknown key triggers abort — committed
    rows stay rotated, the unrotatable row stays in its original (under-k3)
    state, exception propagates."""
    from app.models import Role, User
    from app.models.credential import GitProvider, UserGitCredential
    from app.scripts import rotate_fernet
    from app.services.crypto import TokenCipher

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    k3_unknown = Fernet.generate_key().decode()

    # Row A: encrypted under k1 — rotatable.
    user_a = User(
        email="rotate-3a@x.com",
        handle="rotate-3a",
        role=Role.USER,
        display_name="rotate-3a",
    )
    # Row B: encrypted under k3 — UNROTATABLE.
    user_b = User(
        email="rotate-3b@x.com",
        handle="rotate-3b",
        role=Role.USER,
        display_name="rotate-3b",
    )
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    db_session.add_all(
        [
            UserGitCredential(
                user_id=user_a.id,
                provider=GitProvider.GITHUB,
                encrypted_token=TokenCipher(k1).encrypt("a"),
                token_hint="a",
            ),
            UserGitCredential(
                user_id=user_b.id,
                provider=GitProvider.GITHUB,
                encrypted_token=TokenCipher(k3_unknown).encrypt("b"),
                token_hint="b",
            ),
        ]
    )
    await db_session.commit()

    from tests.conftest import test_session_maker

    monkeypatch.setattr(rotate_fernet, "async_session_maker", test_session_maker)

    with pytest.raises(RuntimeError, match="unrotatable row"):
        await rotate_fernet.rotate_all(k1, k2)


# ---------------------------------------------------------------------------
# Lazy ``async_session_maker`` import — the module-level symbol is None on
# import (so ``--help`` from a fresh dev shell doesn't trigger Settings
# validation); rotate_all populates it from ``app.db`` on first call. The
# rotate_* tests above all monkeypatch the symbol BEFORE calling rotate_all,
# so they never exercise the lazy-import branch (lines 62-64).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotate_all_lazily_imports_session_maker(monkeypatch):
    """Reset the module global to None and verify rotate_all populates it
    from ``app.db.async_session_maker`` on first call (lines 62-64). Point
    app.db's session_maker at the test aiosqlite engine so the lazy import
    resolves to a usable maker rather than the prod asyncpg config."""
    from app.scripts import rotate_fernet

    from tests.conftest import test_session_maker

    monkeypatch.setattr(rotate_fernet, "async_session_maker", None)
    monkeypatch.setattr("app.db.async_session_maker", test_session_maker)

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    # Empty DB — rotate_all returns (0, 0); the side effect we care about is
    # the module global being non-None afterwards.
    rotated, skipped = await rotate_fernet.rotate_all(k1, k2)
    assert (rotated, skipped) == (0, 0)
    assert rotate_fernet.async_session_maker is test_session_maker


# ---------------------------------------------------------------------------
# main() CLI entrypoint — operator-driven, used in the Fernet key rotation
# runbook (docs/runbooks/p3-fernet-rotation.md). Bugs here surface only at
# operator-runtime; pin the exit-code contract.
# ---------------------------------------------------------------------------


def test_main_returns_0_on_successful_rotation(monkeypatch):
    """Happy path: rotate_all completes; main() returns 0 and logs the
    rotated/skipped tallies."""
    from app.scripts import rotate_fernet

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    monkeypatch.setattr("sys.argv", ["rotate_fernet", "--old", k1, "--new", k2])
    monkeypatch.setattr(rotate_fernet, "rotate_all", AsyncMock(return_value=(3, 1)))
    assert rotate_fernet.main() == 0


def test_main_returns_2_on_unrotatable_row(monkeypatch, caplog):
    """rotate_all raising RuntimeError must surface as exit 2 + an error log
    line — distinct from exit 0 so the operator's bash || branch can act."""
    from app.scripts import rotate_fernet

    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    monkeypatch.setattr("sys.argv", ["rotate_fernet", "--old", k1, "--new", k2])
    monkeypatch.setattr(
        rotate_fernet,
        "rotate_all",
        AsyncMock(side_effect=RuntimeError("unrotatable row: user_id=abc")),
    )
    # rotate_fernet.main() configures its own root logger; install a handler
    # on the module logger so caplog picks the message up. Re-use the
    # save/restore .disabled pattern from project_caplog_alembic_logger_disabled.
    rotate_fernet.logger.disabled = False
    with caplog.at_level(logging.ERROR, logger="app.scripts.rotate_fernet"):
        rc = rotate_fernet.main()
    assert rc == 2
    assert any("aborted" in r.message for r in caplog.records)


def test_main_requires_old_and_new(monkeypatch):
    """Argparse must reject a call missing --new / --old with SystemExit
    (argparse's standard behaviour). Pin so a future refactor that changes
    the flag names doesn't silently regress the operator UX."""
    from app.scripts import rotate_fernet

    monkeypatch.setattr("sys.argv", ["rotate_fernet", "--old", "x"])  # missing --new
    with pytest.raises(SystemExit) as exc:
        rotate_fernet.main()
    # argparse exits with status 2 on missing-required-arg.
    assert exc.value.code == 2
