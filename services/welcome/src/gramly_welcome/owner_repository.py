from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from aiogram.types import User
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .crypto import TokenKeyring
from .models import (
    Channel,
    Contact,
    ContentAttachment,
    ContentFlow,
    ContentFlowVersion,
    ContentStep,
    DeliveryOperation,
    EventLog,
    FlowDelivery,
    GreetingDelivery,
    JoinRequest,
    ManagedBot,
    Owner,
    WelcomeDraft,
    WelcomeDraftMedia,
    WelcomeMedia,
    WelcomeMessageVersion,
)


async def owner_from_telegram(session: AsyncSession, user: User) -> Owner:
    values = {
        "telegram_id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "last_seen_at": datetime.now(UTC),
    }
    owner_id = await session.scalar(
        insert(Owner)
        .values(**values)
        .on_conflict_do_update(
            constraint="owner_telegram_id_key",
            set_={key: value for key, value in values.items() if key != "telegram_id"},
        )
        .returning(Owner.id)
    )
    await session.commit()
    owner = await session.get(Owner, owner_id)
    if owner is None:
        raise RuntimeError("Owner upsert did not return a record")
    return owner


async def mark_guide_complete(session: AsyncSession, owner_id: int, steps: int) -> None:
    await session.execute(
        update(Owner).where(Owner.id == owner_id).values(guide_completed=True, guide_step=steps)
    )
    await session.commit()


async def list_owned_bots(
    session: AsyncSession, owner_id: int, offset: int, limit: int
) -> tuple[list[ManagedBot], int]:
    where = (ManagedBot.owner_id == owner_id, ManagedBot.is_active.is_(True))
    total = int(await session.scalar(select(func.count(ManagedBot.id)).where(*where)) or 0)
    bots = list(
        (
            await session.scalars(
                select(ManagedBot)
                .where(*where)
                .order_by(ManagedBot.created_at, ManagedBot.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return bots, total


async def owned_bot(session: AsyncSession, owner_id: int, bot_id: int) -> ManagedBot | None:
    return cast(
        ManagedBot | None,
        await session.scalar(
            select(ManagedBot).where(
                ManagedBot.id == bot_id,
                ManagedBot.owner_id == owner_id,
                ManagedBot.is_active.is_(True),
            )
        ),
    )


async def create_managed_bot(
    session: AsyncSession,
    owner_id: int,
    token: str,
    telegram_user: User,
    settings: Settings,
) -> ManagedBot:
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    bot = ManagedBot(
        owner_id=owner_id,
        public_id=uuid.uuid4(),
        telegram_id=telegram_user.id,
        username=telegram_user.username or "",
        display_name=telegram_user.full_name,
        token_ciphertext=keyring.encrypt(token),
        key_version=keyring.current_version,
        webhook_secret=secrets.token_urlsafe(32),
        path_secret=secrets.token_urlsafe(32),
        is_active=True,
    )
    session.add(bot)
    await session.flush()
    session.add(
        EventLog(
            bot_id=bot.id,
            owner_id=owner_id,
            event_type="bot_registered",
            message="Customer bot registered",
            context={"telegram_bot_id": telegram_user.id},
        )
    )
    await session.commit()
    return bot


async def set_webhook_configured(session: AsyncSession, bot_id: int, configured: bool) -> None:
    await session.execute(
        update(ManagedBot)
        .where(ManagedBot.id == bot_id)
        .values(webhook_configured=configured, updated_at=datetime.now(UTC))
    )
    await session.commit()


async def bot_channels(session: AsyncSession, bot_id: int) -> list[Channel]:
    return list(
        (
            await session.scalars(
                select(Channel)
                .where(Channel.bot_id == bot_id, Channel.is_active.is_(True))
                .order_by(Channel.connected_at, Channel.id)
                .limit(200)
            )
        ).all()
    )


async def bot_statistics(session: AsyncSession, bot_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            select(
                func.count(Contact.id),
                func.count(Contact.id).filter(Contact.delivery_status == "live"),
                func.count(Contact.id).filter(Contact.delivery_status == "dead"),
                func.count(Contact.id).filter(Contact.delivery_status == "unknown"),
            ).where(Contact.bot_id == bot_id)
        )
    ).one()
    channels = int(
        await session.scalar(
            select(func.count(Channel.id)).where(Channel.bot_id == bot_id, Channel.is_active.is_(True))
        )
        or 0
    )
    join_requests = int(
        await session.scalar(select(func.count(JoinRequest.id)).where(JoinRequest.bot_id == bot_id)) or 0
    )
    flow_row = (
        await session.execute(
            select(
                func.count(FlowDelivery.id),
                func.count(FlowDelivery.id).filter(FlowDelivery.status == "completed"),
                func.count(FlowDelivery.id).filter(FlowDelivery.status == "partial"),
                func.count(FlowDelivery.id).filter(FlowDelivery.status == "failed"),
            ).where(FlowDelivery.bot_id == bot_id)
        )
    ).one()
    legacy_row = (
        await session.execute(
            select(
                func.count(GreetingDelivery.id),
                func.count(GreetingDelivery.id).filter(GreetingDelivery.status == "sent"),
                func.count(GreetingDelivery.id).filter(GreetingDelivery.status == "failed"),
            ).where(GreetingDelivery.bot_id == bot_id)
        )
    ).one()
    operation_errors = int(
        await session.scalar(
            select(func.count(DeliveryOperation.id))
            .join(FlowDelivery, FlowDelivery.id == DeliveryOperation.flow_delivery_id)
            .where(
                FlowDelivery.bot_id == bot_id,
                DeliveryOperation.status == "failed",
            )
        )
        or 0
    )
    languages = (
        await session.execute(
            select(Contact.language_code, func.count(Contact.id).label("total"))
            .where(Contact.bot_id == bot_id)
            .group_by(Contact.language_code)
            .order_by(func.count(Contact.id).desc(), Contact.language_code)
            .limit(20)
        )
    ).all()
    return {
        "total": int(row[0] or 0),
        "live": int(row[1] or 0),
        "dead": int(row[2] or 0),
        "unknown": int(row[3] or 0),
        "channels": channels,
        "join_requests": join_requests,
        "deliveries": int(flow_row[0] or 0) + int(legacy_row[0] or 0),
        "delivered": int(flow_row[1] or 0) + int(legacy_row[1] or 0),
        "partial": int(flow_row[2] or 0),
        "failed": int(flow_row[3] or 0) + int(legacy_row[2] or 0),
        "operation_errors": operation_errors,
        "languages": [{"language_code": item.language_code, "total": int(item.total)} for item in languages],
    }


async def pending_requests(session: AsyncSession, bot_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count(JoinRequest.id)).where(
                JoinRequest.bot_id == bot_id,
                JoinRequest.status.in_(("pending", "scheduled", "processing")),
            )
        )
        or 0
    )


async def update_delay(session: AsyncSession, bot_id: int, field: str, value: int) -> None:
    if field not in {"welcome_delay_seconds", "approval_delay_seconds"}:
        raise ValueError("Unsupported delay field")
    await session.execute(
        update(ManagedBot)
        .where(ManagedBot.id == bot_id)
        .values({field: value, "updated_at": datetime.now(UTC)})
    )
    await session.commit()


async def toggle_auto_approve(session: AsyncSession, bot: ManagedBot) -> tuple[bool, int]:
    enabled = not bot.auto_approve
    await session.execute(
        update(ManagedBot)
        .where(ManagedBot.id == bot.id)
        .values(auto_approve=enabled, updated_at=datetime.now(UTC))
    )
    if enabled:
        request_ids = list(
            await session.scalars(
                select(JoinRequest.id).where(JoinRequest.bot_id == bot.id, JoinRequest.status == "pending")
            )
        )
        await session.execute(
            update(JoinRequest)
            .where(JoinRequest.id.in_(request_ids))
            .values(status="scheduled", delay_snapshot_seconds=0, due_at=datetime.now(UTC))
        )
    else:
        request_ids = list(
            await session.scalars(
                select(JoinRequest.id).where(JoinRequest.bot_id == bot.id, JoinRequest.status == "scheduled")
            )
        )
        await session.execute(
            update(JoinRequest).where(JoinRequest.id.in_(request_ids)).values(status="pending", due_at=None)
        )
    await session.commit()
    bot.auto_approve = enabled
    return enabled, len(request_ids)


async def save_welcome_message(
    session: AsyncSession,
    bot_id: int,
    owner_id: int,
    author_telegram_id: int,
    payload: dict[str, Any],
    media: dict[str, Any] | None,
) -> WelcomeMessageVersion:
    await session.execute(select(ManagedBot.id).where(ManagedBot.id == bot_id).with_for_update())
    version_number = (
        int(
            await session.scalar(
                select(func.max(WelcomeMessageVersion.version)).where(WelcomeMessageVersion.bot_id == bot_id)
            )
            or 0
        )
        + 1
    )
    await session.execute(
        update(WelcomeMessageVersion).where(WelcomeMessageVersion.bot_id == bot_id).values(is_active=False)
    )
    version = WelcomeMessageVersion(
        bot_id=bot_id,
        version=version_number,
        author_telegram_id=author_telegram_id,
        payload=payload,
        is_active=True,
    )
    session.add(version)
    await session.flush()
    if media:
        session.add(WelcomeMedia(version_id=version.id, position=0, **media))
    session.add(
        EventLog(
            bot_id=bot_id,
            owner_id=owner_id,
            event_type="welcome_changed",
            message="Welcome message updated",
            context={"version": version_number},
        )
    )
    await session.commit()
    return version


async def append_album_item(
    session: AsyncSession,
    bot_id: int,
    owner_id: int,
    media_group_id: str,
    telegram_message_id: int,
    item_payload: dict[str, Any],
    media: dict[str, Any],
) -> None:
    finalize_at = datetime.now(UTC) + timedelta(seconds=3)
    draft_id = await session.scalar(
        insert(WelcomeDraft)
        .values(
            bot_id=bot_id,
            owner_id=owner_id,
            media_group_id=media_group_id,
            payload={"type": "media_group", "items": []},
            finalize_at=finalize_at,
        )
        .on_conflict_do_update(
            constraint="uq_welcome_draft_group",
            set_={"finalize_at": finalize_at, "updated_at": datetime.now(UTC)},
        )
        .returning(WelcomeDraft.id)
    )
    inserted = await session.scalar(
        insert(WelcomeDraftMedia)
        .values(
            draft_id=draft_id,
            telegram_message_id=telegram_message_id,
            **media,
        )
        .on_conflict_do_nothing(constraint="uq_welcome_draft_message")
        .returning(WelcomeDraftMedia.id)
    )
    if inserted is not None:
        draft = await session.get(WelcomeDraft, draft_id, with_for_update=True)
        if draft is None:
            raise RuntimeError("Welcome album draft disappeared")
        items = list(draft.payload.get("items", []))
        item_payload["telegram_message_id"] = telegram_message_id
        items.append(item_payload)
        items.sort(key=lambda item: int(item["telegram_message_id"]))
        draft.payload = {"type": "media_group", "items": items}
        draft.finalize_at = finalize_at
    await session.commit()


async def finalize_due_albums(session: AsyncSession, limit: int = 20) -> list[tuple[int, int, int, int]]:
    now = datetime.now(UTC)
    drafts = list(
        (
            await session.scalars(
                select(WelcomeDraft)
                .where(WelcomeDraft.finalize_at <= now)
                .order_by(WelcomeDraft.finalize_at, WelcomeDraft.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    completed: list[tuple[int, int, int, int]] = []
    for draft in drafts:
        owner_telegram_id = int(
            await session.scalar(select(Owner.telegram_id).where(Owner.id == draft.owner_id)) or 0
        )
        if draft.finalized_version_id is None:
            await session.execute(
                select(ManagedBot.id).where(ManagedBot.id == draft.bot_id).with_for_update()
            )
            number = (
                int(
                    await session.scalar(
                        select(func.max(WelcomeMessageVersion.version)).where(
                            WelcomeMessageVersion.bot_id == draft.bot_id
                        )
                    )
                    or 0
                )
                + 1
            )
            await session.execute(
                update(WelcomeMessageVersion)
                .where(WelcomeMessageVersion.bot_id == draft.bot_id)
                .values(is_active=False)
            )
            version = WelcomeMessageVersion(
                bot_id=draft.bot_id,
                version=number,
                author_telegram_id=owner_telegram_id,
                payload=draft.payload,
                is_active=True,
            )
            session.add(version)
            await session.flush()
            items = list(
                (
                    await session.scalars(
                        select(WelcomeDraftMedia)
                        .where(WelcomeDraftMedia.draft_id == draft.id)
                        .order_by(
                            WelcomeDraftMedia.telegram_message_id,
                            WelcomeDraftMedia.id,
                        )
                    )
                ).all()
            )
            session.add_all(
                [
                    WelcomeMedia(
                        version_id=version.id,
                        position=position,
                        media_type=item.media_type,
                        storage_key=item.storage_key,
                        original_name=item.original_name,
                        mime_type=item.mime_type,
                        size=item.size,
                    )
                    for position, item in enumerate(items)
                ]
            )
            session.add(
                EventLog(
                    bot_id=draft.bot_id,
                    owner_id=draft.owner_id,
                    event_type="welcome_changed",
                    message="Welcome album updated",
                    context={"version": number},
                )
            )
            draft.finalized_version_id = version.id
        else:
            existing_version = await session.get(WelcomeMessageVersion, draft.finalized_version_id)
            if existing_version is None:
                draft.finalized_version_id = None
                continue
            number = existing_version.version
        # Acts as a short notification lease. Another delivery worker cannot
        # send the same completion message while this worker owns the attempt;
        # a crash makes it retryable after the lease expires.
        draft.finalize_at = now + timedelta(seconds=30)
        completed.append((draft.id, owner_telegram_id, draft.bot_id, number))
    await session.commit()
    return completed


async def complete_album_notification(session: AsyncSession, draft_id: int) -> None:
    await session.execute(delete(WelcomeDraft).where(WelcomeDraft.id == draft_id))
    await session.commit()


async def bot_media_keys(session: AsyncSession, bot_id: int) -> list[str]:
    legacy = (
        select(WelcomeMedia.storage_key.label("storage_key"))
        .join(
            WelcomeMessageVersion,
            WelcomeMessageVersion.id == WelcomeMedia.version_id,
        )
        .where(WelcomeMessageVersion.bot_id == bot_id)
    )
    content = (
        select(ContentAttachment.storage_key.label("storage_key"))
        .join(ContentStep, ContentStep.id == ContentAttachment.step_id)
        .join(ContentFlowVersion, ContentFlowVersion.id == ContentStep.version_id)
        .join(ContentFlow, ContentFlow.id == ContentFlowVersion.flow_id)
        .where(ContentFlow.bot_id == bot_id)
    )
    keys = legacy.union(content).subquery()
    return list(await session.scalars(select(keys.c.storage_key).distinct()))


async def delete_bot(session: AsyncSession, bot_id: int) -> None:
    await session.execute(delete(ManagedBot).where(ManagedBot.id == bot_id))
    await session.commit()
