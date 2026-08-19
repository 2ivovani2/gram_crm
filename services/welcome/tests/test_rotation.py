from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gramly_welcome.models import Channel, ManagedBot, RotationChannel, RotationImpression
from gramly_welcome.rotation import (
    RotationDestination,
    merge_rotation_destinations,
    record_rotation_conversion,
)


def destination(channel_id: int) -> RotationDestination:
    return RotationDestination(
        cast(RotationChannel, SimpleNamespace(channel_id=channel_id)),
        cast(Channel, SimpleNamespace(id=channel_id)),
        cast(ManagedBot, SimpleNamespace(id=channel_id)),
    )


def test_rotation_prefers_priority_and_never_duplicates_or_exceeds_seven() -> None:
    selected = merge_rotation_destinations(
        [destination(1), destination(2)],
        [destination(2), *[destination(item) for item in range(3, 12)]],
    )

    assert [item.channel.id for item in selected] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_rotation_does_not_count_channel_owner_as_conversion() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [9, 12345]

    result = await record_rotation_conversion(
        session,
        destination_channel_id=7,
        telegram_user_id=12345,
        telegram_update_id=99,
        invite_link="https://t.me/+owned",
    )

    assert result is False
    assert session.scalar.await_count == 2


@pytest.mark.asyncio
async def test_rotation_requires_a_matching_prior_impression() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [9, 777, None]

    result = await record_rotation_conversion(
        session,
        destination_channel_id=7,
        telegram_user_id=12345,
        telegram_update_id=99,
        invite_link="https://t.me/+organic",
    )

    assert result is False
    assert session.scalar.await_count == 3


@pytest.mark.asyncio
async def test_rotation_conversion_is_inserted_once() -> None:
    session = AsyncMock(spec=AsyncSession)
    impression = cast(RotationImpression, SimpleNamespace(id=41))
    session.scalar.side_effect = [9, 777, impression, 51]

    result = await record_rotation_conversion(
        session,
        destination_channel_id=7,
        telegram_user_id=12345,
        telegram_update_id=99,
        invite_link="https://t.me/+gramly",
    )

    assert result is True
    assert session.scalar.await_count == 4


@pytest.mark.asyncio
async def test_rotation_rejoin_is_not_counted_twice() -> None:
    session = AsyncMock(spec=AsyncSession)
    impression = cast(RotationImpression, SimpleNamespace(id=41))
    session.scalar.side_effect = [9, 777, impression, None]

    result = await record_rotation_conversion(
        session,
        destination_channel_id=7,
        telegram_user_id=12345,
        telegram_update_id=100,
        invite_link="https://t.me/+gramly",
    )

    assert result is False
