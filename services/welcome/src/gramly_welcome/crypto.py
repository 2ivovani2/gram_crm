from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


class TokenDecryptionError(ValueError):
    pass


def decrypt_legacy_django_token(value: str, secret_key: str) -> str:
    """Decrypt the v1 Django ciphertext during the one-way data migration."""
    if not value.startswith("v1:"):
        raise TokenDecryptionError("Unsupported legacy token version")
    material = f"gramly-welcome:v1:{secret_key}".encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    try:
        return Fernet(key).decrypt(value[3:].encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError("Unable to decrypt a legacy bot token") from exc


@dataclass(frozen=True)
class TokenKeyring:
    keys: dict[int, Fernet]
    current_version: int

    @classmethod
    def parse(cls, value: str) -> TokenKeyring:
        try:
            raw = json.loads(value)
            keys = {int(version): Fernet(str(key).encode()) for version, key in raw.items()}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TokenDecryptionError("WELCOME_TOKEN_ENCRYPTION_KEYS is invalid") from exc
        if not keys:
            raise TokenDecryptionError("WELCOME_TOKEN_ENCRYPTION_KEYS is empty")
        return cls(keys=keys, current_version=max(keys))

    def encrypt(self, token: str) -> str:
        encrypted = self.keys[self.current_version].encrypt(token.encode()).decode()
        return f"v{self.current_version}:{encrypted}"

    def decrypt(self, value: str) -> str:
        prefix, separator, ciphertext = value.partition(":")
        if separator != ":" or not prefix.startswith("v"):
            raise TokenDecryptionError("Unsupported token ciphertext")
        try:
            version = int(prefix[1:])
            key = self.keys[version]
            return key.decrypt(ciphertext.encode()).decode()
        except (ValueError, KeyError, InvalidToken) as exc:
            raise TokenDecryptionError("Unable to decrypt Telegram bot token") from exc
