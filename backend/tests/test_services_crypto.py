import pytest
from app.services.crypto import TokenCipher
from cryptography.fernet import InvalidToken


def test_encrypt_decrypt_roundtrip():
    key = TokenCipher.generate_key()
    cipher = TokenCipher(key)
    plaintext = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    encrypted = cipher.encrypt(plaintext)
    assert isinstance(encrypted, bytes)
    assert cipher.decrypt(encrypted) == plaintext


def test_wrong_key_raises():
    key1 = TokenCipher.generate_key()
    key2 = TokenCipher.generate_key()
    encrypted = TokenCipher(key1).encrypt("hello")
    with pytest.raises(InvalidToken):
        TokenCipher(key2).decrypt(encrypted)


def test_hint_shows_prefix_and_suffix():
    assert (
        TokenCipher.token_hint("ghp_abcdefghijklmnopqrstuvwxyz0123456789")
        == "ghp_...6789"
    )
    assert TokenCipher.token_hint("short") == "sh...rt"
    assert TokenCipher.token_hint("a") == "a"


def test_multifernet_decrypts_with_either_key_in_list():
    """A token encrypted with k1 must decrypt under MultiFernet([k2, k1])
    (rotation window) and FAIL under MultiFernet([k2]) (rotation complete)."""
    k_old = TokenCipher.generate_key()
    k_new = TokenCipher.generate_key()
    ciphertext = TokenCipher(k_old).encrypt("hello")

    # Rotation window: decrypt with [new, old] succeeds.
    assert TokenCipher([k_new, k_old]).decrypt(ciphertext) == "hello"

    # Rotation complete: decrypt with [new] only fails.
    with pytest.raises(InvalidToken):
        TokenCipher([k_new]).decrypt(ciphertext)


def test_multifernet_encrypts_with_first_key():
    """The leading key in the list is the active encrypt key; ciphertext is
    decryptable by that key alone."""
    k1 = TokenCipher.generate_key()
    k2 = TokenCipher.generate_key()
    ciphertext = TokenCipher([k1, k2]).encrypt("hello")

    # k1 alone decrypts (it was the encrypt key).
    assert TokenCipher(k1).decrypt(ciphertext) == "hello"
    # k2 alone cannot decrypt — it was only in the trial set for *future*
    # rotations, never used for encrypt.
    with pytest.raises(InvalidToken):
        TokenCipher(k2).decrypt(ciphertext)


def test_empty_keys_iterable_raises_value_error():
    """Pydantic / chart sometimes hand us an empty list (misconfigured deploy).
    The constructor must fail loud, not silently fall through to a Fernet
    crash deep inside encrypt()."""
    with pytest.raises(ValueError, match="at least one"):
        TokenCipher([])


def test_single_key_construction_still_supported():
    """Existing call sites pass a single str/bytes key — backward-compat."""
    key = TokenCipher.generate_key()
    ciphertext = TokenCipher(key).encrypt("hello")
    assert TokenCipher(key).decrypt(ciphertext) == "hello"

    # Also accepts str-key.
    key_str = key.decode()
    assert TokenCipher(key_str).encrypt("x") != b""
