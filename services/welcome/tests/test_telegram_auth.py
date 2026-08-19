from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from gramly_welcome.telegram_auth import TelegramInitDataError, verify_init_data

BOT_TOKEN = "123456:testing-token"


def signed_init_data(
    *,
    now: datetime,
    user: dict[str, object] | None = None,
    token: str = BOT_TOKEN,
) -> str:
    values = {
        "auth_date": str(int(now.timestamp())),
        "query_id": "AAE-test",
        "user": json.dumps(
            user
            or {
                "id": 42,
                "first_name": "Alex",
                "last_name": "Gramly",
                "username": "alex_test",
                "language_code": "ru",
            },
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_verifies_telegram_init_data() -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    verified = verify_init_data(signed_init_data(now=now), BOT_TOKEN, max_age_seconds=300, now=now)

    assert verified.user.id == 42
    assert verified.user.username == "alex_test"
    assert verified.user.language_code == "ru"
    assert verified.query_id == "AAE-test"


def test_rejects_forged_init_data() -> None:
    now = datetime.now(UTC)
    raw = signed_init_data(now=now).replace("alex_test", "attacker")
    with pytest.raises(TelegramInitDataError, match="signature"):
        verify_init_data(raw, BOT_TOKEN, max_age_seconds=300, now=now)


def test_rejects_expired_init_data() -> None:
    now = datetime.now(UTC)
    raw = signed_init_data(now=now - timedelta(seconds=301))
    with pytest.raises(TelegramInitDataError, match="expired"):
        verify_init_data(raw, BOT_TOKEN, max_age_seconds=300, now=now)


def test_rejects_duplicate_fields_before_authentication() -> None:
    now = datetime.now(UTC)
    raw = f"auth_date=1&{signed_init_data(now=now)}"
    with pytest.raises(TelegramInitDataError, match="Duplicate"):
        verify_init_data(raw, BOT_TOKEN, max_age_seconds=300, now=now)


def test_rejects_future_init_data() -> None:
    now = datetime.now(UTC)
    raw = signed_init_data(now=now + timedelta(seconds=31))
    with pytest.raises(TelegramInitDataError, match="expired"):
        verify_init_data(raw, BOT_TOKEN, max_age_seconds=300, now=now)
