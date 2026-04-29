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
