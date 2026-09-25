from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.bot_diagnostics import record_incident, resolve_incident
from gramly_welcome.flow_delivery import cancel_delivery_operations, close_temporary_delivery
from gramly_welcome.models import (
    Channel,
    Contact,
    ContentFlow,
    ContentFlowVersion,
    ContentStep,
    DeliveryOperation,
    FlowDelivery,
    InboxEvent,
    JoinRequest,
    ManagedBot,
    Owner,
    OwnerNotification,
)
from gramly_welcome.processor import process_event

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


@pytest.mark.asyncio
async def test_terminal_flow_cancels_orphaned_processing_operation() -> None:
    assert DATABASE_URL
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().int % 1_000_000_000
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        owner = Owner(telegram_id=-(80_000_000_000 + unique))
        session.add(owner)
        await session.flush()
        bot = ManagedBot(owner_id=owner.id, public_id=uuid.uuid4(),
                         telegram_id=-(81_000_000_000 + unique), display_name="Test",
                         token_ciphertext="test", webhook_secret=uuid.uuid4().hex,
                         path_secret=uuid.uuid4().hex)
        session.add(bot)
        await session.flush()
        channel = Channel(bot_id=bot.id, telegram_id=-unique, title="Test")
        contact = Contact(bot_id=bot.id, telegram_id=unique)
        flow = ContentFlow(bot_id=bot.id, name="Test", kind="welcome")
        session.add_all([channel, contact, flow])
        await session.flush()
        version = ContentFlowVersion(flow_id=flow.id, version=1, status="published",
                                     author_telegram_id=unique)
        session.add(version)
        await session.flush()
        step = ContentStep(version_id=version.id, position=0, payload={"text": "hello"})
        delivery = FlowDelivery(bot_id=bot.id, channel_id=channel.id, contact_id=contact.id,
                                version_id=version.id, event_key="orphan", status="partial",
                                completed_at=now, target_chat_id=unique + 1,
                                target_expires_at=now - timedelta(minutes=1))
        session.add_all([step, delivery])
        await session.flush()
        session.add_all([
            DeliveryOperation(flow_delivery_id=delivery.id, step_id=step.id, position=0,
                              operation_type="text", status="sent", due_at=now),
            DeliveryOperation(flow_delivery_id=delivery.id, step_id=step.id, position=1,
                              operation_type="text", status="processing", due_at=now,
                              lease_owner="stale-worker", lease_expires_at=now - timedelta(seconds=1)),
        ])
        owner_id, delivery_id = owner.id, delivery.id
    try:
        async with sessions() as session:
            assert await cancel_delivery_operations(session, delivery_id,
                                                     reason="delivery_already_finished") == "partial"
            assert await cancel_delivery_operations(session, delivery_id,
                                                     reason="delivery_already_finished") == "partial"
            row = await session.get(FlowDelivery, delivery_id)
            operations = list(await session.scalars(select(DeliveryOperation)
                .where(DeliveryOperation.flow_delivery_id == delivery_id)
                .order_by(DeliveryOperation.position)))
            assert row and row.status == "partial" and row.completed_at == now
            assert [item.status for item in operations] == ["sent", "cancelled"]
            assert operations[1].lease_owner is None and operations[1].lease_expires_at is None
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(FlowDelivery).where(FlowDelivery.id == delivery_id))
            await session.execute(delete(Owner).where(Owner.id == owner_id))
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("known_contact", [False, True])
async def test_closed_request_cancels_only_unsent_or_switches_to_confirmed_address(known_contact: bool) -> None:
    assert DATABASE_URL
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    unique = uuid.uuid4().int % 1_000_000_000
    now = datetime.now(UTC)
    second_delivery_id: int | None = None
    async with sessions() as session, session.begin():
        owner = Owner(telegram_id=-(70_000_000_000 + unique))
        session.add(owner)
        await session.flush()
        bot = ManagedBot(owner_id=owner.id, public_id=uuid.uuid4(), telegram_id=-(71_000_000_000 + unique),
                         display_name="Test", token_ciphertext="test", webhook_secret=uuid.uuid4().hex,
                         path_secret=uuid.uuid4().hex, auto_approve=False)
        session.add(bot)
        await session.flush()
        channel = Channel(bot_id=bot.id, telegram_id=-unique, title="Test")
        contact = Contact(bot_id=bot.id, telegram_id=unique, bot_started=known_contact)
        flow = ContentFlow(bot_id=bot.id, name="Test", kind="welcome")
        session.add_all([channel, contact, flow])
        await session.flush()
        version = ContentFlowVersion(flow_id=flow.id, version=1, status="published", author_telegram_id=unique)
        session.add(version)
        await session.flush()
        step = ContentStep(version_id=version.id, position=0, payload={"text": "hello"})
        delivery = FlowDelivery(bot_id=bot.id, channel_id=channel.id, contact_id=contact.id,
                                version_id=version.id, event_key="test", target_chat_id=unique + 1,
                                target_expires_at=now + timedelta(minutes=5))
        session.add_all([step, delivery])
        await session.flush()
        session.add_all([DeliveryOperation(flow_delivery_id=delivery.id, step_id=step.id, position=index,
                                          operation_type="sendMessage", status=status, due_at=now)
                         for index, status in enumerate(("sent", "scheduled"))])
        session.add(JoinRequest(bot_id=bot.id, channel_id=channel.id, contact_id=contact.id,
                               telegram_update_id=100, welcome_delivery_id=delivery.id, status="pending"))
        owner_id, bot_id, delivery_id = owner.id, bot.id, delivery.id
    try:
        # Closing inside a rolled-back membership transaction must not cancel delivery.
        async with sessions() as session:
            event = InboxEvent(bot_id=bot_id, update_id=101, payload={"chat_member": {
                "chat": {"id": -unique, "title": "Test"},
                "from": {"id": 999999, "is_bot": True},
                "old_chat_member": {"status": "left", "user": {"id": unique}},
                "new_chat_member": {"status": "member", "user": {"id": unique}},
            }})
            await process_event(session, event)
            await process_event(session, event)
            await session.rollback()
        async with sessions() as session, session.begin():
            row = await session.get(FlowDelivery, delivery_id)
            assert row and row.target_chat_id is not None and row.status == "scheduled"
            await process_event(session, event)
            await process_event(session, event)
        async with sessions() as session:
            row = await session.get(FlowDelivery, delivery_id)
            statuses = list(await session.scalars(select(DeliveryOperation.status)
                            .where(DeliveryOperation.flow_delivery_id == delivery_id).order_by(DeliveryOperation.position)))
            assert statuses == ["sent", "scheduled" if known_contact else "cancelled"]
            assert row and row.status == ("scheduled" if known_contact else "partial")
            if known_contact:
                assert row.target_chat_id is None and row.target_expires_at is None
            assert not await close_temporary_delivery(session, delivery_id)
            assert await session.scalar(select(func.count(FlowDelivery.id)).where(FlowDelivery.bot_id == bot_id)) == 1
            current_bot = await session.get(ManagedBot, bot_id)
            assert current_bot and current_bot.auto_approve is False
            assert len(current_bot.delivery_issues) == (0 if known_contact else 1)
            if not known_contact:
                issue = current_bot.delivery_issues[f"external_approval:{channel.id}"]
                assert issue["approver_id"] == 999999
                assert issue["sent_steps"] == 1
                assert issue["cancelled_steps"] == 1
                original_id = issue["id"]
                await record_incident(session, bot_id, "external_approval", channel_id=channel.id,
                                      approver_id=888888, approver_username="second_bot", delivery_id=delivery_id)
                await resolve_incident(session, bot_id, f"external_approval:{channel.id}", expected_id="stale")
                await session.commit()
                await session.refresh(current_bot)
                assert current_bot.delivery_issues[f"external_approval:{channel.id}"]["id"] == original_id
                assert current_bot.delivery_issues[f"external_approval:{channel.id}"]["approver_username"] == "second_bot"
                assert await session.scalar(select(func.count(OwnerNotification.id)).where(
                    OwnerNotification.owner_id == owner_id,
                    OwnerNotification.kind == "delivery_incident",
                )) == 1

        if not known_contact:
            # A later approval by Gramly does not prove that the competing bot stopped.
            async with sessions() as session, session.begin():
                second_contact = Contact(bot_id=bot_id, telegram_id=unique + 2)
                session.add(second_contact)
                await session.flush()
                second_delivery = FlowDelivery(
                    bot_id=bot_id, channel_id=channel.id, contact_id=second_contact.id,
                    version_id=version.id, event_key="own-approval", target_chat_id=unique + 12,
                    target_expires_at=now + timedelta(minutes=5),
                )
                session.add(second_delivery)
                await session.flush()
                second_delivery_id = second_delivery.id
                session.add(JoinRequest(
                    bot_id=bot_id, channel_id=channel.id, contact_id=second_contact.id,
                    telegram_update_id=102, welcome_delivery_id=second_delivery.id, status="pending",
                ))
            async with sessions() as session, session.begin():
                await process_event(session, InboxEvent(bot_id=bot_id, update_id=103, payload={"chat_member": {
                    "chat": {"id": -unique, "title": "Test"},
                    "from": {"id": bot.telegram_id, "is_bot": True},
                    "old_chat_member": {"status": "left", "user": {"id": unique + 2}},
                    "new_chat_member": {"status": "member", "user": {"id": unique + 2}},
                }}))
            async with sessions() as session:
                refreshed_bot = await session.get(ManagedBot, bot_id)
                assert refreshed_bot and f"external_approval:{channel.id}" in refreshed_bot.delivery_issues

        async def incident() -> None:
            async with sessions() as session, session.begin():
                await record_incident(session, bot_id, "invalid_bot_token")

        await asyncio.gather(*(incident() for _ in range(4)))
        async with sessions() as session, session.begin():
            assert await session.scalar(select(func.count(OwnerNotification.id)).where(
                OwnerNotification.owner_id == owner_id, OwnerNotification.payload["body"].astext.contains("Токен"))) == 1
            await resolve_incident(session, bot_id, "invalid_bot_token")
        await incident()
        async with sessions() as session:
            assert await session.scalar(select(func.count(OwnerNotification.id)).where(
                OwnerNotification.owner_id == owner_id, OwnerNotification.payload["body"].astext.contains("Токен"))) == 2
    finally:
        async with sessions() as session, session.begin():
            # Remove only fixture-owned rows, in restrictive-FK order.
            if second_delivery_id is not None:
                await session.execute(delete(FlowDelivery).where(FlowDelivery.id == second_delivery_id))
            await session.execute(delete(FlowDelivery).where(FlowDelivery.id == delivery_id))
            await session.execute(delete(Owner).where(Owner.id == owner_id))
        await engine.dispose()
