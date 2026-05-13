"""User-PAT symmetric encryption (Fernet / MultiFernet).

Wraps cryptography's ``MultiFernet`` for key rotation. Construct with a single
key (str/bytes) for the common single-key case, or with an iterable of keys
to enable rotation: the FIRST key is used for encrypt; all keys are tried
for decrypt. The operator deploys a new key in front of the old in
``FERNET_KEYS``, runs ``python -m app.scripts.rotate_fernet --old K_old
--new K_new`` to re-encrypt every row, then retires the old key in a
follow-up upgrade.
"""

from collections.abc import Iterable

from cryptography.fernet import Fernet, MultiFernet


class TokenCipher:
    """Symmetric cipher for storing PATs encrypted at rest."""

    def __init__(
        self,
        keys: str | bytes | Iterable[str | bytes],
    ) -> None:
        # Single str/bytes wraps to a one-element list. Anything else is
        # treated as an iterable of keys.
        if isinstance(keys, str | bytes):
            key_list: list[str | bytes] = [keys]
        else:
            key_list = list(keys)
        if not key_list:
            raise ValueError("TokenCipher requires at least one Fernet key")
        fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in key_list]
        self._fernet: Fernet | MultiFernet = (
            fernets[0] if len(fernets) == 1 else MultiFernet(fernets)
        )

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
