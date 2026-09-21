from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.learning import (
    claim_notification,
    finish_notification,
    navigate_tip_session,
    open_help_session,
    schedule_learning_notifications,
)
from gramly_welcome.models import Announcement, ContextualHelp, Owner, OwnerNotification, Tip

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


@pytest.mark.asyncio
async def test_announcements_are_durable_without_legacy_onboarding() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions() as session:
        owner = Owner(telegram_id=-13001, username="learning-integration")
        announcement = Announcement(
            title="Release",
            body="New feature",
            audience="all",
            starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(days=1),
            is_active=True,
        )
        session.add_all([owner, announcement])
        await session.commit()

        assert await schedule_learning_notifications(session, owner.id, now=now) == 1
        assert await schedule_learning_notifications(session, owner.id, now=now) == 0

        first = await claim_notification(session, "test-worker", now=now)
        assert first is not None
        notification, telegram_id = first
        assert telegram_id == owner.telegram_id
        assert notification.kind == "announcement"
        await finish_notification(session, notification.id, error="temporary", now=now)
        await session.refresh(owner)
        await session.refresh(notification)
        assert not owner.guide_completed
        assert notification.status == "retry"
        assert notification.due_at > now

        assert await session.scalar(
            select(OwnerNotification.id).where(
                OwnerNotification.owner_id == owner.id,
                OwnerNotification.announcement_id == announcement.id,
            )
        ) == notification.id
        help_item = ContextualHelp(
            feature_key="integration-help",
            title="Integration help",
            body="Body",
            is_active=True,
        )
        tips = [
            Tip(feature_key="integration-help", text="First", sort_order=1),
            Tip(feature_key="integration-help", text="Second", sort_order=2),
        ]
        session.add_all([help_item, *tips])
        await session.commit()
        opened = await open_help_session(session, owner.id, "integration-help", now=now)
        assert opened.session_id is not None
        assert opened.tip is not None
        next_tip = await navigate_tip_session(
            session, owner.id, opened.session_id, 1, now=now
        )
        previous_tip = await navigate_tip_session(
            session, owner.id, opened.session_id, -1, now=now
        )
        assert next_tip is not None and previous_tip is not None
        assert next_tip.tip is not None and next_tip.tip.id != opened.tip.id
        assert previous_tip.tip is not None and previous_tip.tip.id == opened.tip.id

        await session.execute(delete(Owner).where(Owner.id == owner.id))
        await session.execute(delete(Announcement).where(Announcement.id == announcement.id))
        await session.execute(delete(ContextualHelp).where(ContextualHelp.id == help_item.id))
        await session.execute(delete(Tip).where(Tip.feature_key == "integration-help"))
        await session.commit()
    await engine.dispose()
