from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl


class TelegramInitDataError(ValueError):
    """Raised when Telegram Mini App initData cannot be trusted."""


@dataclass(frozen=True)
class TelegramMiniAppUser:
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    language_code: str = ""


@dataclass(frozen=True)
class VerifiedInitData:
    user: TelegramMiniAppUser
    auth_date: datetime
    query_id: str
    start_param: str


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def verify_init_data(
    raw: str,
    bot_token: str,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> VerifiedInitData:
    if not raw or not bot_token:
        raise TelegramInitDataError("Mini App authentication is unavailable")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise TelegramInitDataError("Malformed Telegram initData") from exc
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise TelegramInitDataError("Duplicate Telegram initData field")
        values[key] = value
    supplied_hash = values.pop("hash", "")
    if len(supplied_hash) != 64:
        raise TelegramInitDataError("Telegram initData hash is missing")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    expected_hash = hmac.new(_secret_key(bot_token), data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise TelegramInitDataError("Telegram initData signature is invalid")

    try:
        auth_timestamp = int(values["auth_date"])
        raw_user = json.loads(values["user"])
        user_id = int(raw_user["id"])
        first_name = str(raw_user["first_name"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TelegramInitDataError("Telegram initData user is invalid") from exc
    current = now or datetime.now(UTC)
    auth_date = datetime.fromtimestamp(auth_timestamp, tz=UTC)
    age = (current - auth_date).total_seconds()
    if age < -30 or age > max_age_seconds:
        raise TelegramInitDataError("Telegram initData has expired")
    return VerifiedInitData(
        user=TelegramMiniAppUser(
            id=user_id,
            first_name=first_name,
            last_name=str(raw_user.get("last_name") or ""),
            username=str(raw_user.get("username") or ""),
            language_code=str(raw_user.get("language_code") or ""),
        ),
        auth_date=auth_date,
        query_id=values.get("query_id", ""),
        start_param=values.get("start_param", ""),
    )
