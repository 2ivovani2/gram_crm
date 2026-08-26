from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.content_service import ContentValidationError, clone_bot_configuration
from gramly_welcome.models import (
    Channel,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentKeyboard,
    ContentKeyboardButton,
    ContentStep,
    FlowChannelAssignment,
    ManagedBot,
    Owner,
    RotationChannel,
)

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


def _bot(owner_id: int, suffix: int, *, auto_approve: bool = False) -> ManagedBot:
    return ManagedBot(
        owner_id=owner_id,
        public_id=uuid.uuid4(),
        telegram_id=-340_000 - suffix,
        username=f"template_bot_{suffix}",
        display_name=f"Template bot {suffix}",
        token_ciphertext="encrypted",
        webhook_secret=f"webhook-{suffix}",
        path_secret=f"path-{suffix}",
        is_active=True,
        webhook_configured=True,
        welcome_delay_seconds=42 if auto_approve else 0,
        approval_delay_seconds=17 if auto_approve else 0,
        auto_approve=auto_approve,
    )


@pytest.mark.asyncio
async def test_bot_template_copies_configuration_without_operational_data() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner = Owner(telegram_id=-34_001, username="bot-template-integration")
        session.add(owner)
        await session.flush()
        source = _bot(owner.id, 1, auto_approve=True)
        destination = _bot(owner.id, 2)
        session.add_all([source, destination])
        await session.flush()
        channel = Channel(bot_id=source.id, telegram_id=-100_340_001, title="Source channel")
        flow = ContentFlow(
            bot_id=source.id,
            name="Основная цепочка",
            kind="welcome",
            assignment_mode="selected",
            is_active=True,
        )
        session.add_all([channel, flow])
        await session.flush()
        version = ContentFlowVersion(
            flow_id=flow.id,
            version=3,
            status="published",
            author_telegram_id=owner.telegram_id,
            first_delay_seconds=11,
        )
        session.add(version)
        await session.flush()
        step = ContentStep(
            version_id=version.id,
            position=0,
            payload={"type": "text", "text": "Привет, {name}!"},
            delay_after_seconds=19,
        )
        session.add(step)
        await session.flush()
        session.add(
            ContentAttachment(
                step_id=step.id,
                position=0,
                media_type="photo",
                storage_key="templates/shared-photo.jpg",
                payload={"telegram_file_unique_id": "shared"},
            )
        )
        keyboard = ContentKeyboard(step_id=step.id, kind="inline", settings={})
        session.add(keyboard)
        await session.flush()
        session.add(
            ContentKeyboardButton(
                keyboard_id=keyboard.id,
                row=0,
                position=0,
                text="Открыть",
                action_type="url",
                value="https://gramly.tech",
                style="primary",
            )
        )
        session.add(FlowChannelAssignment(flow_id=flow.id, channel_id=channel.id))
        await session.commit()

        result = await clone_bot_configuration(
            session,
            owner.id,
            source.id,
            destination.id,
            business_enabled=True,
        )

        assert (result.flows, result.versions, result.steps) == (1, 1, 1)
        await session.refresh(destination)
        assert destination.auto_approve is True
        assert destination.welcome_delay_seconds == 42
        assert destination.approval_delay_seconds == 17
        target_flow = await session.scalar(
            select(ContentFlow).where(ContentFlow.bot_id == destination.id)
        )
        assert target_flow is not None
        assert target_flow.assignment_mode == "selected"
        target_version = await session.scalar(
            select(ContentFlowVersion).where(ContentFlowVersion.flow_id == target_flow.id)
        )
        assert target_version is not None and target_version.legacy_version_id is None
        target_step = await session.scalar(
            select(ContentStep).where(ContentStep.version_id == target_version.id)
        )
        assert target_step is not None and target_step.payload["text"] == "Привет, {name}!"
        assert await session.scalar(
            select(func.count(ContentAttachment.id)).where(ContentAttachment.step_id == target_step.id)
        ) == 1
        assert await session.scalar(
            select(func.count(Channel.id)).where(Channel.bot_id == destination.id)
        ) == 0
        assert await session.scalar(
            select(func.count(FlowChannelAssignment.id)).where(
                FlowChannelAssignment.flow_id == target_flow.id
            )
        ) == 0

        blocked_destination = _bot(owner.id, 3)
        session.add(blocked_destination)
        session.add(
            RotationChannel(
                owner_id=owner.id,
                bot_id=source.id,
                channel_id=channel.id,
                is_enabled=True,
            )
        )
        await session.commit()
        with pytest.raises(ContentValidationError, match="Business-ротация"):
            await clone_bot_configuration(
                session,
                owner.id,
                source.id,
                blocked_destination.id,
                business_enabled=False,
            )
        await session.rollback()
        assert await session.scalar(
            select(func.count(ContentFlow.id)).where(ContentFlow.bot_id == blocked_destination.id)
        ) == 0

        await session.execute(delete(Owner).where(Owner.id == owner.id))
        await session.commit()
    await engine.dispose()
