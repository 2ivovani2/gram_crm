"""Versioned encryption for Owners Center operational secrets."""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    material = f"gramly-owners:v1:{settings.SECRET_KEY}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_secret(value: str) -> str:
    value = value.strip()
    return "" if not value else "v1:" + _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("v1:"):
        raise ValueError("Unsupported Owners Center secret version")
    try:
        return _fernet().decrypt(value[3:].encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt Owners Center secret") from exc
