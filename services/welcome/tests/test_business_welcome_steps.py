from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gramly_welcome import flow_delivery
from gramly_welcome.catalog_starters import starter_entries
from gramly_welcome.content_service import (
    ContentValidationError,
    _validate_flow_limits,
    flow_step_limit,
    flow_timeline_seconds,
)
from gramly_welcome.editor_api import SaveDraft
from gramly_welcome.message_catalog import CatalogEntry
from gramly_welcome.models import (
    Channel,
    Contact,
    ContentFlow,
    ContentFlowVersion,
    ContentStep,
    DeliveryOperation,
    ManagedBot,
    Owner,
    Plan,
    Subscription,
)


@pytest.mark.parametrize(
    ("kind", "plan", "limit"),
    [
        ("welcome", "free", 5),
        ("welcome", "business", 10),
        ("farewell", "free", 5),
        ("farewell", "business", 5),
    ],
)
def test_step_limit_only_expands_business_welcome(kind: str, plan: str, limit: int) -> None:
    assert flow_step_limit(kind, plan) == limit
    _validate_flow_limits(limit, 5, max_steps=limit)
    with pytest.raises(ContentValidationError, match=f"не более {limit} сообщений"):
        _validate_flow_limits(limit + 1, 0, max_steps=limit)
    with pytest.raises(ContentValidationError, match="не более 5 файлов"):
        _validate_flow_limits(1, 6, max_steps=limit)


def test_editor_schema_allows_ten_but_not_eleven_steps() -> None:
    payload: dict[str, Any] = {
        "expected_revision": 1,
        "first_delay_seconds": 0,
        "steps": [{"payload": {"text": "Привет"}}] * 10,
    }
    assert len(SaveDraft.model_validate(payload).steps) == 10
    with pytest.raises(ValidationError):
        SaveDraft.model_validate({**payload, "steps": payload["steps"] + payload["steps"][:1]})


@pytest.mark.parametrize("count", [6, 10])
def test_long_catalog_template_requires_business_welcome(count: int) -> None:
    base = starter_entries()[0]
    spec = {"steps": [{"payload": {"text": "Привет"}}] * count}
    entry = CatalogEntry.model_validate(
        {**base, "kind": "welcome", "requires_business": True, "spec": spec}
    )
    assert len(entry.spec.steps) == count
    for kind, business in (("welcome", False), ("farewell", True)):
        with pytest.raises(ValidationError, match="Business-приветствия"):
            CatalogEntry.model_validate(
                {**base, "kind": kind, "requires_business": business, "spec": spec}
            )
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            {**base, "kind": "welcome", "requires_business": True,
             "spec": {"steps": [{"payload": {"text": "Привет"}}] * 11}}
        )


@pytest.mark.asyncio
async def test_downgrade_truncates_only_new_welcome_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    steps = [ContentStep(position=i, delay_after_seconds=10) for i in range(10)]
    access = AsyncMock(return_value=SimpleNamespace(plan_slug="free"))
    monkeypatch.setattr(flow_delivery, "access_for_owner", access)
    shortened = await flow_delivery.effective_delivery_steps(
        SimpleNamespace(), owner_id=7, kind="welcome", steps=steps  # type: ignore[arg-type]
    )
    assert shortened == steps[:5]
    assert flow_timeline_seconds(ContentFlowVersion(first_delay_seconds=0), shortened) == 40
    assert len(steps) == 10  # The published version and existing delivery jobs are untouched.

    access.return_value = SimpleNamespace(plan_slug="business")
    restored = await flow_delivery.effective_delivery_steps(
        SimpleNamespace(), owner_id=7, kind="welcome", steps=steps  # type: ignore[arg-type]
    )
    assert restored == steps
    assert flow_timeline_seconds(ContentFlowVersion(first_delay_seconds=0), restored) == 90
    assert await flow_delivery.effective_delivery_steps(
        SimpleNamespace(), owner_id=7, kind="farewell", steps=steps  # type: ignore[arg-type]
    ) == steps


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("WELCOME_TEST_DATABASE_URL"), reason="PostgreSQL required")
async def test_scheduled_jobs_keep_ten_before_downgrade_and_restore_after_renewal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ["WELCOME_TEST_DATABASE_URL"]
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(flow_delivery, "choose_free_ad", AsyncMock(return_value=None))
    try:
        async with sessions() as session:
            suffix = uuid.uuid4().int % 1_000_000_000
            owner = Owner(telegram_id=-suffix, username=f"business-steps-{suffix}")
            session.add(owner)
            await session.flush()
            business = await session.scalar(select(Plan).where(Plan.slug == "business"))
            assert business is not None
            now = datetime.now(UTC)
            subscription = Subscription(
                owner_id=owner.id, plan_id=business.id, source="manual", status="active",
                starts_at=now - timedelta(days=2), ends_at=now + timedelta(days=1),
            )
            bot = ManagedBot(
                owner_id=owner.id, public_id=uuid.uuid4(), telegram_id=-suffix - 1,
                display_name="Ten steps", token_ciphertext="encrypted", webhook_secret="secret",
                path_secret="path", is_active=True,
            )
            session.add_all([subscription, bot])
            await session.flush()
            channel = Channel(bot_id=bot.id, telegram_id=-suffix - 2, title="Test channel")
            contact = Contact(bot_id=bot.id, telegram_id=suffix + 1, first_name="Test")
            flow = ContentFlow(bot_id=bot.id, name="Welcome", kind="welcome", assignment_mode="all")
            session.add_all([channel, contact, flow])
            await session.flush()
            version = ContentFlowVersion(
                flow_id=flow.id, version=1, status="published", author_telegram_id=owner.telegram_id,
                first_delay_seconds=0,
            )
            session.add(version)
            await session.flush()
            session.add_all([
                ContentStep(version_id=version.id, position=i,
                            payload={"type": "text", "text": f"Step {i}"}, delay_after_seconds=10)
                for i in range(10)
            ])
            await session.flush()

            async def schedule(key: str) -> list[DeliveryOperation]:
                delivery_id = await flow_delivery.schedule_content_flow(
                    session, bot=bot, channel_id=channel.id, contact_id=contact.id,
                    event_key=key, target_chat_id=suffix + 2,
                    target_expires_at=now + timedelta(minutes=5), max_timeline_seconds=240,
                )
                assert delivery_id is not None
                return list(await session.scalars(
                    select(DeliveryOperation).where(DeliveryOperation.flow_delivery_id == delivery_id)
                    .order_by(DeliveryOperation.position)
                ))

            original = await schedule("business-ten")
            assert len(original) == 10
            assert await flow_delivery.assigned_content_flow_timeline(
                session, bot_id=bot.id, channel_id=channel.id
            ) == 90
            subscription.ends_at = now - timedelta(days=1)
            await session.flush()
            free = await schedule("free-five")
            assert len(free) == 5
            assert [job.payload["text"] for job in free] == [f"Step {i}" for i in range(5)]
            assert await flow_delivery.assigned_content_flow_timeline(
                session, bot_id=bot.id, channel_id=channel.id
            ) == 40
            assert len(list(await session.scalars(
                select(DeliveryOperation).where(
                    DeliveryOperation.flow_delivery_id == original[0].flow_delivery_id
                )
            ))) == 10
            subscription.ends_at = now + timedelta(days=1)
            await session.flush()
            assert len(await schedule("renewed-ten")) == 10
            await session.rollback()
    finally:
        await engine.dispose()
