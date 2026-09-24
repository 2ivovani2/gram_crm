from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot

from gramly_welcome.bot_diagnostics import diagnostics_text
from gramly_welcome.models import Channel, ManagedBot
from gramly_welcome.transfer_preflight import competing_admins


def test_existing_delivery_issue_identifies_channel_approver_and_steps() -> None:
    bot = SimpleNamespace(delivery_issues={
        "external_approval:7": {
            "channel_id": 7, "channel_title": "News", "approver_username": "other_bot",
            "sent_steps": 2, "cancelled_steps": 3,
        },
    })
    text = diagnostics_text(cast(ManagedBot, bot))
    assert "News" in text and "@other_bot" in text
    assert "Отправлено шагов: 2; отменено: 3" in text


@pytest.mark.asyncio
async def test_admin_check_distinguishes_possible_competitor_from_unavailable() -> None:
    client = SimpleNamespace(get_chat_administrators=AsyncMock(return_value=[
        SimpleNamespace(user=SimpleNamespace(id=1, is_bot=True, username="gramly"), can_invite_users=True),
        SimpleNamespace(user=SimpleNamespace(id=2, is_bot=True, username="other"), can_invite_users=True),
        SimpleNamespace(user=SimpleNamespace(id=3, is_bot=True, username="harmless"), can_invite_users=False),
        SimpleNamespace(user=SimpleNamespace(id=4, is_bot=False, username="owner"), can_invite_users=True),
    ]))
    channel = SimpleNamespace(telegram_id=-1007)
    result = await competing_admins(cast(Bot, client), cast(Channel, channel), 1)
    assert result == {"status": "checked", "bots": [{"id": 2, "username": "other"}]}
    client.get_chat_administrators.assert_awaited_with(-1007, return_bots=True)
    client.get_chat_administrators.side_effect = TimeoutError()
    assert await competing_admins(cast(Bot, client), cast(Channel, channel), 1) == {"status": "unavailable", "bots": []}
