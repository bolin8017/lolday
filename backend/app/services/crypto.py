from cryptography.fernet import Fernet


class TokenCipher:
    """Wraps Fernet symmetric encryption for user PATs."""

    def __init__(self, key: str | bytes) -> None:
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()

    @staticmethod
    def token_hint(token: str) -> str:
        """Human-readable hint that does not reveal the full token."""
        if len(token) <= 2:
            return token
        if len(token) <= 8:
            return f"{token[:2]}...{token[-2:]}"
        return f"{token[:4]}...{token[-4:]}"
