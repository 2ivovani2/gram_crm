from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.broadcasts import cancel_broadcast, launch_broadcast, save_broadcast
from gramly_welcome.models import (
    BroadcastRecipient,
    Channel,
    Contact,
    ManagedBot,
    Owner,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_broadcast_launch_is_idempotent_and_snapshots_only_active_consent() -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_id: int | None = None
    try:
        async with sessions() as session:
            owner = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
            session.add(owner)
            await session.flush()
            owner_id = owner.id
            bot = ManagedBot(
                owner_id=owner.id,
                public_id=uuid.uuid4(),
                telegram_id=owner.telegram_id,
                username="broadcast_test_bot",
                display_name="Broadcast test",
                token_ciphertext="test",
                webhook_secret="test",
                path_secret="test",
            )
            session.add(bot)
            await session.flush()
            channel = Channel(bot_id=bot.id, telegram_id=owner.telegram_id, title="Source")
            session.add(channel)
            await session.flush()
            session.add_all(
                [
                    Contact(
                        bot_id=bot.id,
                        telegram_id=owner.telegram_id - 1,
                        first_name="Active",
                        bot_started=True,
                        consent_status="active",
                        source_channel_id=channel.id,
                    ),
                    Contact(
                        bot_id=bot.id,
                        telegram_id=owner.telegram_id - 2,
                        first_name="Stopped",
                        bot_started=True,
                        consent_status="unsubscribed",
                        source_channel_id=channel.id,
                    ),
                ]
            )
            await session.commit()

            content: dict[str, Any] = {
                "payload": {"type": "text", "text": "Проверка", "link_preview_enabled": False},
                "attachments": [],
                "keyboard": None,
            }
            first = await save_broadcast(
                session,
                owner_id=owner.id,
                bot_id=bot.id,
                source_channel_id=channel.id,
                name="Snapshot",
                content=content,
                idempotency_key="stable-test-key",
            )
            repeated = await save_broadcast(
                session,
                owner_id=owner.id,
                bot_id=bot.id,
                source_channel_id=channel.id,
                name="Snapshot",
                content=content,
                idempotency_key="stable-test-key",
            )
            assert repeated.id == first.id

            launched, recipients = await launch_broadcast(
                session,
                owner_id=owner.id,
                broadcast_id=first.id,
                scheduled_at=None,
            )
            assert launched.status == "scheduled"
            assert recipients == 1
            assert await session.scalar(
                select(func.count()).select_from(BroadcastRecipient).where(
                    BroadcastRecipient.broadcast_id == first.id
                )
            ) == 1

            repeated_launch, repeated_count = await launch_broadcast(
                session,
                owner_id=owner.id,
                broadcast_id=first.id,
                scheduled_at=None,
            )
            assert repeated_launch.id == first.id
            assert repeated_count == 1

            await cancel_broadcast(session, owner_id=owner.id, broadcast_id=first.id)
            statuses = set(
                await session.scalars(
                    select(BroadcastRecipient.status).where(
                        BroadcastRecipient.broadcast_id == first.id
                    )
                )
            )
            assert statuses == {"cancelled"}
    finally:
        if owner_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(Owner).where(Owner.id == owner_id))
        await engine.dispose()
