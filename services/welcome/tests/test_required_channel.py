from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import ChatMemberUpdated

from gramly_welcome.models import FeatureFlag, Owner, RequiredChannelMembership
from gramly_welcome.required_channel import (
    check_required_membership,
    record_required_membership_update,
)


def _flag(*, enabled: bool = True) -> FeatureFlag:
    return FeatureFlag(
        key="required_news_channel",
        enabled=enabled,
        config={
            "channel_id": -1004404255750,
            "title": "GRAMLY | Новости",
            "url": "https://t.me/+gSu2vmXMeWBmZjI6",
        },
    )


@pytest.mark.asyncio
async def test_disabled_gate_never_calls_telegram() -> None:
    session = AsyncMock()
    session.get.return_value = _flag(enabled=False)
    bot = AsyncMock()

    result = await check_required_membership(
        session, bot, Owner(id=1, telegram_id=42, first_name="Ира")
    )

    assert result.allowed is True
    bot.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_membership_is_rechecked_after_sixty_seconds() -> None:
    now = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    cached = RequiredChannelMembership(
        owner_id=1,
        channel_id=-1004404255750,
        status="member",
        is_member=True,
        checked_at=now - timedelta(seconds=30),
    )
    session = AsyncMock()
    session.get.return_value = _flag()
    session.scalar.return_value = cached
    bot = AsyncMock()

    fresh = await check_required_membership(
        session, bot, Owner(id=1, telegram_id=42, first_name="Ира"), now=now
    )
    assert fresh.allowed is True
    bot.get_chat_member.assert_not_awaited()

    bot.get_chat_member.return_value = SimpleNamespace(status="left", is_member=False)
    persisted = SimpleNamespace(checked_at=now + timedelta(seconds=31))
    with patch("gramly_welcome.required_channel._persist", AsyncMock(return_value=persisted)):
        expired = await check_required_membership(
            session,
            bot,
            Owner(id=1, telegram_id=42, first_name="Ира"),
            now=now + timedelta(seconds=31),
        )
    assert expired.allowed is False


@pytest.mark.asyncio
async def test_explicit_check_accepts_new_subscription() -> None:
    session = AsyncMock()
    session.get.return_value = _flag()
    session.scalar.return_value = None
    bot = AsyncMock()
    bot.get_chat_member.return_value = SimpleNamespace(status="member", is_member=True)
    persisted = SimpleNamespace(checked_at=datetime.now(UTC))

    with patch("gramly_welcome.required_channel._persist", AsyncMock(return_value=persisted)):
        result = await check_required_membership(
            session,
            bot,
            Owner(id=1, telegram_id=42, first_name="Ира"),
            force=True,
        )

    assert result.allowed is True
    bot.get_chat_member.assert_awaited_once_with(-1004404255750, 42)


@pytest.mark.asyncio
async def test_chat_member_update_records_unsubscribe_immediately() -> None:
    session = AsyncMock()
    session.get.return_value = _flag()
    owner = Owner(id=7, telegram_id=42, first_name="Ира")
    session.scalar.return_value = owner
    update = SimpleNamespace(
        chat=SimpleNamespace(id=-1004404255750),
        new_chat_member=SimpleNamespace(
            user=SimpleNamespace(id=42),
            status="left",
            is_member=False,
        ),
    )
    persist = AsyncMock(return_value=SimpleNamespace())

    with patch("gramly_welcome.required_channel._persist", persist):
        handled = await record_required_membership_update(
            session, cast(ChatMemberUpdated, update)
        )

    assert handled is True
    persist.assert_awaited_once()
    call = persist.await_args
    assert call is not None
    assert call.kwargs["status"] == "left"
    assert call.kwargs["is_member"] is False
