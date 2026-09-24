"""Read-only transfer diagnostics. Telegram checks never mutate chat permissions."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .crypto import TokenDecryptionError, TokenKeyring
from .models import (
    Channel,
    ContentFlow,
    ContentFlowVersion,
    DeliveryJob,
    EventLog,
    FlowChannelAssignment,
    ManagedBot,
)


async def competing_admins(bot: Bot, channel: Channel, own_telegram_id: int) -> dict[str, Any]:
    try:
        # Telegram omits other bots unless this flag is explicit. Without it,
        # an empty result can falsely clear an observed approval conflict.
        admins = await bot.get_chat_administrators(channel.telegram_id, return_bots=True)
    except (TelegramAPIError, OSError, TimeoutError):
        return {"status": "unavailable", "bots": []}
    others = [
        {"id": member.user.id, "username": member.user.username or ""}
        for member in admins
        if member.user.is_bot and member.user.id != own_telegram_id
        and bool(getattr(member, "can_invite_users", False))
    ]
    return {"status": "checked", "bots": others}


async def live_transfer_checks(
    managed: ManagedBot, channels: list[Channel], settings: Settings,
) -> dict[str, Any]:
    result: dict[str, Any] = {"webhook": {"status": "unavailable"}, "administrators": {}}
    try:
        token = TokenKeyring.parse(settings.token_encryption_keys).decrypt(managed.token_ciphertext)
        async with Bot(token=token, session=AiohttpSession(timeout=5)) as bot:
            try:
                info = await bot.get_webhook_info()
                expected = f"{settings.public_webhook_base_url.rstrip('/')}/{managed.public_id}/{managed.path_secret}/"
                result["webhook"] = {
                    "status": "ok" if info.url == expected else "mismatch",
                    "pending_updates": info.pending_update_count,
                }
            except (TelegramAPIError, OSError, TimeoutError):
                pass
            semaphore = asyncio.Semaphore(4)

            async def check(channel: Channel) -> tuple[int, dict[str, Any]]:
                async with semaphore:
                    return channel.id, await competing_admins(bot, channel, managed.telegram_id)

            result["administrators"] = dict(await asyncio.gather(*(check(c) for c in channels)))
    except (TokenDecryptionError, ValueError):
        result["webhook"] = {"status": "invalid_credential"}
    return result


async def transfer_preview(session: AsyncSession, managed: ManagedBot, settings: Settings) -> dict[str, Any]:
    channels = list((await session.scalars(
        select(Channel).where(Channel.bot_id == managed.id, Channel.is_active.is_(True)).order_by(Channel.id)
    )).all())
    published = list((await session.execute(
        select(ContentFlow.assignment_mode, FlowChannelAssignment.channel_id)
        .join(ContentFlowVersion, ContentFlowVersion.flow_id == ContentFlow.id)
        .outerjoin(FlowChannelAssignment, FlowChannelAssignment.flow_id == ContentFlow.id)
        .where(ContentFlow.bot_id == managed.id, ContentFlow.kind == "welcome",
               ContentFlow.is_active.is_(True), ContentFlowVersion.status == "published")
    )).all())
    active_channel_ids = {c.id for c in channels}
    welcome_channels = {
        channel_id for mode, channel_id in published
        if channel_id in active_channel_ids and mode == "selected"
    }
    if any(mode == "all" for mode, _ in published):
        welcome_channels = active_channel_ids
    issues = managed.delivery_issues or {}
    since = datetime.now(UTC) - timedelta(days=30)
    events = list((await session.scalars(
        select(EventLog).where(
            EventLog.bot_id == managed.id,
            EventLog.event_type.in_(("delivery_incident_opened", "delivery_incident_observed")),
            EventLog.message == "external_approval",
            EventLog.created_at >= since,
        ).order_by(EventLog.created_at.desc()).limit(10)
    )).all())
    pending_jobs = int(await session.scalar(
        select(func.count(DeliveryJob.id)).where(
            DeliveryJob.bot_id == managed.id,
            DeliveryJob.status.in_(("pending", "signaled", "processing", "retry")),
        )
    ) or 0)
    live = await live_transfer_checks(managed, channels, settings)
    channel_rows = []
    for channel in channels:
        admin_result = live["administrators"].get(channel.id, {"status": "unavailable", "bots": []})
        channel_rows.append({
            "id": channel.id, "title": channel.title, "greeting_active": channel.id in welcome_channels,
            "can_invite_users": channel.can_invite_users,
            "incident": issues.get(f"external_approval:{channel.id}"),
            "admin_check": admin_result,
        })
    return {
        "bot_id": managed.id, "is_active": managed.is_active,
        "webhook": live["webhook"], "channels": channel_rows,
        "other_issues": [key for key in issues if not key.startswith("external_approval:")],
        "pending_jobs": pending_jobs,
        "recent_approvals": [
            {"at": event.created_at.isoformat(), "channel_id": event.context.get("channel_id"),
             "approver_username": event.context.get("approver_username") or "",
             "sent_steps": event.context.get("sent_steps"),
             "cancelled_steps": event.context.get("cancelled_steps")}
            for event in events
        ],
    }
