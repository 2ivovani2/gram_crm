from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome.app_api import (
    CurrentWebUser,
    LearningProgressRequest,
    learning_resources,
    update_learning_progress,
)
from gramly_welcome.learning import (
    claim_notification,
    finish_notification,
    navigate_tip_session,
    open_help_session,
    schedule_learning_notifications,
)
from gramly_welcome.models import (
    Announcement,
    ContextualHelp,
    LearningVideo,
    Owner,
    OwnerNotification,
    Tip,
)
from gramly_welcome.web_sessions import AuthenticatedWebSession

DATABASE_URL = os.getenv("WELCOME_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="WELCOME_TEST_DATABASE_URL is not set")


def _web_user(owner: Owner) -> CurrentWebUser:
    return CurrentWebUser(
        AuthenticatedWebSession(
            session_id=uuid.uuid4(),
            owner=owner,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )


@pytest.mark.asyncio
async def test_learning_progress_is_clamped_idempotent_and_private() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    slug = f"learning-progress-{uuid.uuid4().hex}"
    first_owner_id: int | None = None
    second_owner_id: int | None = None
    video_id: int | None = None
    try:
        async with sessions() as session:
            first = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
            second = Owner(telegram_id=-(uuid.uuid4().int % 1_000_000_000))
            video = LearningVideo(
                slug=slug,
                title="Progress test",
                video_url="https://example.com/progress.mp4",
                duration_seconds=100,
                chapter_key="first_launch",
                sort_order=99_999,
            )
            session.add_all([first, second, video])
            await session.commit()
            first_owner_id, second_owner_id, video_id = first.id, second.id, video.id

            first_user, second_user = _web_user(first), _web_user(second)
            halfway = await update_learning_progress(
                video.id,
                LearningProgressRequest(position_seconds=50),
                first_user,
                session,
            )
            assert halfway["position_seconds"] == 50
            assert halfway["completed_at"] is None

            completed = await update_learning_progress(
                video.id,
                LearningProgressRequest(position_seconds=90),
                first_user,
                session,
            )
            assert completed["position_seconds"] == 90
            assert completed["completed_at"] is not None

            repeated = await update_learning_progress(
                video.id,
                LearningProgressRequest(position_seconds=20),
                first_user,
                session,
            )
            assert repeated["position_seconds"] == 90
            assert repeated["completed_at"] == completed["completed_at"]

            own_catalog = await learning_resources(first_user, session)
            foreign_catalog = await learning_resources(second_user, session)
            own_videos = cast(list[dict[str, object]], own_catalog["videos"])
            foreign_videos = cast(list[dict[str, object]], foreign_catalog["videos"])
            own_video = next(item for item in own_videos if item["id"] == video.id)
            foreign_video = next(item for item in foreign_videos if item["id"] == video.id)
            assert own_video["position_seconds"] == 90
            assert own_video["completed_at"] is not None
            assert foreign_video["position_seconds"] == 0
            assert foreign_video["completed_at"] is None
    finally:
        async with sessions() as session, session.begin():
            if video_id is not None:
                await session.execute(delete(LearningVideo).where(LearningVideo.id == video_id))
            for owner_id in (first_owner_id, second_owner_id):
                if owner_id is not None:
                    await session.execute(delete(Owner).where(Owner.id == owner_id))
        await engine.dispose()


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
