from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.models import (
    Channel,
    Contact,
    InboxEvent,
    InboxSource,
    JoinRequest,
    ManagedBot,
    Owner,
)
from gramly_welcome.processor import _join_request_upsert
from gramly_welcome.reconcile_join_requests import reconcile

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


@pytest.mark.asyncio
async def test_partial_index_upsert_replay_and_concurrency() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().int % 1_000_000_000
    now = datetime.now(UTC)
    async with sessions() as session:
        owner = Owner(telegram_id=-(8_000_000_000 + unique), username="join-pg-test")
        session.add(owner)
        await session.flush()
        bot = ManagedBot(
            owner_id=owner.id,
            public_id=uuid.uuid4(),
            telegram_id=-(9_000_000_000 + unique),
            display_name="Join integration",
            token_ciphertext="test",
            webhook_secret=uuid.uuid4().hex,
            path_secret=uuid.uuid4().hex,
            auto_approve=False,
        )
        session.add(bot)
        await session.flush()
        channel = Channel(
            bot_id=bot.id,
            telegram_id=-(10_000_000_000 + unique),
            title="Join test",
        )
        contact = Contact(bot_id=bot.id, telegram_id=11_000_000_000 + unique)
        session.add_all([channel, contact])
        await session.commit()
        owner_id, bot_id, channel_id, contact_id = owner.id, bot.id, channel.id, contact.id

    async def upsert(update_id: int, user_chat_id: int) -> int:
        async with sessions() as session:
            persisted_bot = await session.get(ManagedBot, bot_id)
            assert persisted_bot is not None
            request_id = await session.scalar(
                _join_request_upsert(
                    bot=persisted_bot,
                    channel_id=channel_id,
                    contact_id=contact_id,
                    update_id=update_id,
                    user_chat_id=user_chat_id,
                    message_window_expires_at=now + timedelta(minutes=5),
                    due_at=None,
                    now=now,
                )
            )
            await session.commit()
            assert request_id is not None
            return int(request_id)

    first_id = await upsert(100, 1000)
    replay_id = await upsert(101, 1001)
    concurrent_ids = await asyncio.gather(upsert(102, 1002), upsert(103, 1003))
    assert {first_id, replay_id, *concurrent_ids} == {first_id}

    async with sessions() as session:
        assert await session.scalar(
            select(func.count(JoinRequest.id)).where(
                JoinRequest.channel_id == channel_id,
                JoinRequest.contact_id == contact_id,
            )
        ) == 1
        request = await session.scalar(
            select(JoinRequest).where(JoinRequest.id == first_id)
        )
        assert request is not None
        assert request.telegram_update_id in {102, 103}
        assert request.user_chat_id in {1002, 1003}
        loaded_bot = await session.get(ManagedBot, bot_id)
        assert loaded_bot is not None
        loaded_bot.auto_approve = True
        request.status = "failed"
        request.error = "TelegramBadRequest"
        source_key = f"join-test-{unique}"
        session.add(InboxSource(source_key=source_key, bot_id=bot_id))
        session.add(
            InboxEvent(
                source_key=source_key,
                bot_id=bot_id,
                update_id=200,
                payload={"chat_join_request": {"chat": {"id": -1}}},
                status="dead",
                last_error="ProgrammingError",
            )
        )
        await session.commit()

        dry_run = await reconcile(session, apply=False)
        assert (dry_run.inbox_events, dry_run.approval_requests) == (1, 1)
        applied = await reconcile(session, apply=True)
        assert (applied.inbox_events, applied.approval_requests) == (1, 1)
        replay = await reconcile(session, apply=True)
        assert (replay.inbox_events, replay.approval_requests) == (0, 0)
        await session.refresh(request)
        assert request.status == "scheduled"
        recovered_event = await session.scalar(
            select(InboxEvent).where(InboxEvent.source_key == source_key)
        )
        assert recovered_event is not None and recovered_event.status == "retry"
        await session.execute(delete(Owner).where(Owner.id == owner_id))
        await session.commit()
    await engine.dispose()
