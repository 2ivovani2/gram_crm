from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.content_service import ContentValidationError, open_draft, set_chain_button_style
from gramly_welcome.flow_delivery import keyboard_payload
from gramly_welcome.models import (
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    ManagedBot,
    Owner,
)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_chain_color_is_versioned_owned_and_preserves_legacy() -> None:
    engine = create_async_engine(os.environ["WELCOME_TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = None
    try:
        async with sessions() as session:
            owner = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
            session.add(owner)
            await session.flush()
            owner_id = owner.id
            bot = ManagedBot(owner_id=owner.id, public_id=uuid.uuid4(), telegram_id=owner.telegram_id,
                             display_name="Color test",
                             token_ciphertext="test", webhook_secret="test", path_secret="test")
            session.add(bot)
            await session.flush()
            flow = ContentFlow(bot_id=bot.id, kind="welcome", name="Color test", assignment_mode="selected")
            session.add(flow)
            await session.flush()
            published = ContentFlowVersion(flow_id=flow.id, version=1, status="published", author_telegram_id=owner.telegram_id)
            session.add(published)
            await session.flush()
            step = ContentStep(version_id=published.id, position=0, payload={"type":"text","text":"Hello"})
            session.add(step)
            await session.flush()
            keyboard = ContentKeyboard(step_id=step.id, kind="inline")
            session.add(keyboard)
            await session.flush()
            session.add(ContentKeyboardButton(keyboard_id=keyboard.id, row=0, position=0,
                text="Open", action_type="url", value="https://gramly.tech", style="danger"))
            await session.commit()
            legacy = await keyboard_payload(session, step.id)
            assert legacy and legacy["rows"][0][0]["style"] == "danger"
            draft = await open_draft(session, owner.id, flow.id, owner.telegram_id)
            draft_step = await session.scalar(select(ContentStep).where(ContentStep.version_id == draft.id))
            assert draft_step is not None
            for style in ["default", "primary", "success", "danger"]:
                await set_chain_button_style(session, owner.id, draft.id, style)
                actual = await keyboard_payload(session, draft_step.id)
                assert actual and actual["rows"][0][0]["style"] == style
                original = await keyboard_payload(session, step.id)
                assert original == legacy
            for target_owner, version, style in [(owner.id+1000,draft.id,"success"), (owner.id,published.id,"success"), (owner.id,draft.id,"purple")]:
                with pytest.raises(ContentValidationError):
                    await set_chain_button_style(session,target_owner,version,style)
                await session.rollback()
    finally:
        async with sessions() as session, session.begin():
            if owner_id:
                await session.execute(delete(ContentFlow).where(ContentFlow.bot_id.in_(select(ManagedBot.id).where(ManagedBot.owner_id==owner_id))))
                await session.execute(delete(ManagedBot).where(ManagedBot.owner_id==owner_id))
                await session.execute(delete(Owner).where(Owner.id==owner_id))
        await engine.dispose()
