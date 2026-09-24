from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.bot_transfer import transfer_bot
from gramly_welcome.config import Settings
from gramly_welcome.models import (
    Broadcast,
    DeliveryJob,
    ManagedBot,
    Owner,
    OwnerNotification,
    WelcomeDraft,
)
from gramly_welcome.transfer_preflight import transfer_preview


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_bot_transfer_moves_product_state_and_keeps_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    source_id: int | None = None
    target_id: int | None = None
    try:
        async with sessions() as session:
            suffix = uuid.uuid4().int % 1_000_000_000
            source = Owner(telegram_id=-suffix, username=f"source_{suffix}")
            target = Owner(telegram_id=-(suffix + 1), username=f"target_{suffix}")
            session.add_all([source, target])
            await session.flush()
            source_id, target_id = source.id, target.id
            bot = ManagedBot(
                owner_id=source.id,
                public_id=uuid.uuid4(),
                telegram_id=-(suffix + 2),
                username=f"transfer_{suffix}_bot",
                display_name="Transfer test",
                token_ciphertext="encrypted-token-must-stay",
                webhook_secret="webhook",
                path_secret="path",
                delivery_issues={"external_approval:123": {"id": "known", "channel_id": 123, "channel_title": "Test channel"}},
            )
            session.add(bot)
            await session.flush()
            draft = WelcomeDraft(
                bot_id=bot.id,
                owner_id=source.id,
                media_group_id=f"transfer-{suffix}",
                payload={},
                finalize_at=datetime.now(UTC) + timedelta(minutes=1),
            )
            broadcast = Broadcast(
                owner_id=source.id,
                bot_id=bot.id,
                name="Transfer",
                content={},
                status="draft",
                idempotency_key=f"transfer-{suffix}",
            )
            session.add_all([draft, broadcast])
            job = DeliveryJob(
                source_kind="transfer-test", source_id=suffix, owner_id=source.id, bot_id=bot.id,
                chat_id=None, lane="urgent", priority=0, status="pending", due_at=datetime.now(UTC),
                next_attempt_at=datetime.now(UTC), dedupe_key=f"transfer-test:{suffix}",
            )
            session.add(job)
            await session.commit()

            result = await transfer_bot(
                session,
                bot_id=bot.id,
                target_owner_id=target.id,
                admin_username="integration-test",
                reason="Owner requested a complete transfer",
            )
            await session.commit()

            await session.refresh(bot)
            await session.refresh(draft)
            await session.refresh(broadcast)
            await session.refresh(job)
            assert result.source_owner_id == source.id
            assert bot.owner_id == target.id
            assert bot.token_ciphertext == "encrypted-token-must-stay"
            assert draft.owner_id == target.id
            assert broadcast.owner_id == target.id
            assert job.owner_id == target.id
            assert bot.webhook_secret == "webhook" and bot.path_secret == "path"
            target_notice = await session.scalar(select(OwnerNotification).where(
                OwnerNotification.owner_id == target.id, OwnerNotification.kind == "bot_transfer"))
            assert target_notice and "Test channel" in target_notice.payload["body"]
            async def fake_live_checks(*_args: object) -> dict:
                return {"webhook": {"status": "ok"}, "administrators": {}}
            monkeypatch.setattr("gramly_welcome.transfer_preflight.live_transfer_checks", fake_live_checks)
            preview = await transfer_preview(session, bot, Settings())
            assert preview["pending_jobs"] == 1
            assert preview["webhook"]["status"] == "ok"
            assert len(
                list(
                    (
                        await session.scalars(
                            select(OwnerNotification).where(
                                OwnerNotification.owner_id.in_((source.id, target.id)),
                                OwnerNotification.kind == "bot_transfer",
                            )
                        )
                    ).all()
                )
            ) == 2
    finally:
        if target_id is not None or source_id is not None:
            async with sessions() as session:
                if target_id is not None:
                    await session.execute(delete(Owner).where(Owner.id == target_id))
                if source_id is not None:
                    await session.execute(delete(Owner).where(Owner.id == source_id))
                await session.commit()
        await engine.dispose()
