from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from gramly_welcome import owner_bot, webhook_admin
from gramly_welcome.config import Settings
from gramly_welcome.models import ManagedBot


@pytest.mark.parametrize("value", ["", "45.146.131.207", " 45.146.131.207 "])
def test_webhook_ip_allows_blank_or_explicit_ipv4(value: str) -> None:
    assert Settings(webhook_ip_address=value).webhook_ip_address == value.strip()


@pytest.mark.parametrize("value", ["gramly.tech", "https://45.146.131.207", "::1", "999.1.1.1"])
def test_webhook_ip_rejects_invalid_configuration(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(webhook_ip_address=value)


class FakeBot:
    webhook_calls: list[dict[str, Any]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> FakeBot:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def set_webhook(self, _url: str, **kwargs: Any) -> bool:
        self.webhook_calls.append(kwargs)
        return True

    async def set_my_name(self, **_kwargs: Any) -> None:
        return None

    async def set_my_short_description(self, **_kwargs: Any) -> None:
        return None

    async def set_my_description(self, **_kwargs: Any) -> None:
        return None

    async def set_my_commands(self, **_kwargs: Any) -> None:
        return None

    async def set_chat_menu_button(self, **_kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_customer_webhook_uses_configured_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBot.webhook_calls.clear()
    monkeypatch.setattr(owner_bot, "Bot", FakeBot)
    managed = SimpleNamespace(
        public_id=uuid.uuid4(),
        path_secret="path-secret",
        webhook_secret="webhook-secret",
    )

    await owner_bot.configure_customer_webhook(
        cast(ManagedBot, managed),
        "123:token",
        Settings(webhook_ip_address="45.146.131.207"),
    )

    assert FakeBot.webhook_calls[-1]["ip_address"] == "45.146.131.207"
    assert FakeBot.webhook_calls[-1]["drop_pending_updates"] is False


@pytest.mark.asyncio
async def test_interface_webhook_uses_configured_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBot.webhook_calls.clear()
    monkeypatch.setattr(webhook_admin, "Bot", FakeBot)
    monkeypatch.setattr(
        webhook_admin,
        "get_settings",
        lambda: Settings(
            interface_bot_token="123:token",
            interface_webhook_secret="webhook-secret",
            webhook_ip_address="45.146.131.207",
        ),
    )

    async def skip_photo(_token: str) -> None:
        return None

    monkeypatch.setattr(webhook_admin, "_set_profile_photo", skip_photo)
    await webhook_admin.reconcile()

    assert FakeBot.webhook_calls[-1]["ip_address"] == "45.146.131.207"
    assert FakeBot.webhook_calls[-1]["drop_pending_updates"] is False
