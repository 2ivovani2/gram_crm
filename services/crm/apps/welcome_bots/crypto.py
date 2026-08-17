"""Small, versioned encryption wrapper for customer Telegram bot tokens."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class TokenDecryptionError(ValueError):
    pass


def _fernet() -> Fernet:
    # A dedicated env key can be introduced without changing stored values by
    # retaining SECRET_KEY as the default. Tokens are never logged or exposed.
    material = f"gramly-welcome:v1:{settings.SECRET_KEY}".encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return "v1:" + _fernet().encrypt(token.encode()).decode()


def decrypt_token(value: str) -> str:
    if not value.startswith("v1:"):
        raise TokenDecryptionError("Unsupported token encryption version")
    try:
        return _fernet().decrypt(value[3:].encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError("Unable to decrypt Telegram bot token") from exc
