from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.admin_dashboard_metrics import high_activity_owner_ids, reachable_contacts
from gramly_welcome.models import Contact, ManagedBot, Owner


def test_weekly_threshold_deduplicates_owner_and_telegram_user() -> None:
    sql = str(high_activity_owner_ids(datetime.now(UTC)).compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
    assert "DISTINCT" in sql
    assert "managed_bot.owner_id" in sql
    assert "consented_at" in sql
    assert "bot_started" in sql


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_weekly_owner_threshold_and_reachable_audience_after_transfer() -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_ids: list[int] = []
    try:
        async with sessions() as session:
            nonce = uuid.uuid4().int % 1_000_000_000
            first = Owner(telegram_id=-nonce)
            second = Owner(telegram_id=-(nonce + 1))
            session.add_all([first, second])
            await session.flush()
            owner_ids = [first.id, second.id]
            bots = [
                ManagedBot(
                    owner_id=first.id, public_id=uuid.uuid4(), telegram_id=-(nonce + i + 2),
                    username=f"dashboard_{i}", display_name="Dashboard test", token_ciphertext="test",
                    webhook_secret="test", path_secret="test", is_active=True,
                ) for i in range(2)
            ]
            session.add_all(bots)
            await session.flush()
            now = datetime.now(UTC)
            contacts = [
                Contact(
                    bot_id=bots[0].id, telegram_id=-(nonce + 1000 + i), bot_started=True,
                    consent_status="active", consented_at=now - timedelta(hours=1), delivery_status="live",
                ) for i in range(100)
            ]
            contacts += [
                Contact(
                    bot_id=bots[1].id, telegram_id=-(nonce + 1000 + i), bot_started=True,
                    consent_status="active", consented_at=now - timedelta(hours=1), delivery_status="live",
                ) for i in range(20)
            ]
            contacts += [
                Contact(bot_id=bots[1].id, telegram_id=-(nonce + 2000), bot_started=True,
                        consent_status="unsubscribed", consented_at=now, delivery_status="live"),
                Contact(bot_id=bots[1].id, telegram_id=-(nonce + 2001), bot_started=True,
                        consent_status="active", consented_at=now, delivery_status="dead"),
                Contact(bot_id=bots[1].id, telegram_id=-(nonce + 2002), bot_started=False,
                        consent_status="active", consented_at=now, delivery_status="live"),
                Contact(bot_id=bots[1].id, telegram_id=-(nonce + 2003), bot_started=True,
                        consent_status="active", consented_at=now - timedelta(days=8), delivery_status="live"),
            ]
            session.add_all(contacts)
            await session.flush()

            selected = list(await session.scalars(high_activity_owner_ids(now)))
            assert first.id in selected
            count = await session.scalar(
                select(func.count(func.distinct(Contact.telegram_id)))
                .join(ManagedBot, ManagedBot.id == Contact.bot_id)
                .where(ManagedBot.owner_id == first.id, *reachable_contacts())
            )
            assert count == 101  # 100 recent unique plus one older consent

            bots[0].owner_id = second.id
            await session.flush()
            selected_after_transfer = list(await session.scalars(high_activity_owner_ids(now)))
            assert first.id not in selected_after_transfer
            assert second.id in selected_after_transfer

            bots[0].is_active = False
            await session.flush()
            assert second.id not in list(await session.scalars(high_activity_owner_ids(now)))
            await session.rollback()
    finally:
        if owner_ids:
            async with sessions() as session:
                await session.execute(delete(Owner).where(Owner.id.in_(owner_ids)))
                await session.commit()
        await engine.dispose()
