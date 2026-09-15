"""Read-only client audit. Never approves requests or changes Telegram settings."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from sqlalchemy import select, text

from .config import get_settings
from .crypto import TokenKeyring
from .db import session_factory
from .models import Channel, ManagedBot
from .telegram_errors import CLIENT_FAILURES, PLATFORM_FAILURES, UNKNOWN_FAILURES, normalize_delivery_error


def safe_reason(value: str) -> str:
    # Legacy rows may contain provider messages: never echo them into an audit.
    return value if value in CLIENT_FAILURES | PLATFORM_FAILURES | UNKNOWN_FAILURES else "other"


async def inspect_telegram(bot: Any, managed: Any, channels: list[Any], base_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {"bot_id": managed.id, "auto_approve": managed.auto_approve, "channels": []}
    try:
        me = await bot.get_me()
        if me.id != managed.telegram_id:
            return {**result, "token": "token_identity_mismatch"}
        result["token"] = "ok"
    except Exception as exc:
        return {**result, "token": normalize_delivery_error(exc)}
    try:
        webhook = await bot.get_webhook_info()
        expected = f"{base_url.rstrip('/')}/{managed.public_id}/{managed.path_secret}/"
        result["webhook"] = {
            "configured": bool(webhook.url), "matches_expected_url": webhook.url == expected,
            "pending_updates": webhook.pending_update_count,
            "last_error_at": webhook.last_error_date.isoformat() if webhook.last_error_date else None,
            # The error text and URL can contain credentials and are intentionally omitted.
            "has_error": bool(webhook.last_error_message),
            "allowed_updates": webhook.allowed_updates,
        }
    except Exception as exc:
        result["webhook"] = {"error": normalize_delivery_error(exc)}
    for channel in channels:
        row: dict[str, Any] = {"channel_id": channel.id, "stored_can_invite_users": channel.can_invite_users}
        try:
            member = await bot.get_chat_member(chat_id=channel.telegram_id, user_id=managed.telegram_id)
            row.update(status=member.status, can_invite_users=getattr(member, "can_invite_users", None),
                       can_send_welcome_messages=getattr(member, "can_send_welcome_messages", None))
        except Exception as exc:
            row["error"] = normalize_delivery_error(exc)
        result["channels"].append(row)
    return result


async def audit(*, hours: int, bot_id: int | None) -> None:
    since = datetime.now(UTC) - timedelta(hours=hours)
    settings = get_settings()
    keyring = TokenKeyring.parse(settings.token_encryption_keys)
    async with session_factory() as session, session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        query = select(ManagedBot).where(ManagedBot.is_active.is_(True)).order_by(ManagedBot.id)
        if bot_id is not None:
            query = query.where(ManagedBot.id == bot_id)
        bots = list((await session.scalars(query)).all())
        channels = list((await session.scalars(select(Channel).where(Channel.is_active.is_(True)))).all())
        # Materialize only explicit non-sensitive fields; never payloads or contact IDs.
        summaries: dict[int, Any] = {}
        for managed in bots:
            parameters = {"bot": managed.id, "since": since}
            summary: dict[str, Any] = {}
            for name, sql in {
                "inbox": "SELECT status, count(*) FROM inbox_event WHERE bot_id=:bot AND received_at>=:since GROUP BY status",
                "requests": "SELECT status, count(*) FROM join_request WHERE bot_id=:bot AND created_at>=:since GROUP BY status",
                "deliveries": "SELECT status, count(*) FROM flow_delivery WHERE bot_id=:bot AND created_at>=:since GROUP BY status",
                "operation_errors": "SELECT o.error, count(*) FROM delivery_operation o JOIN flow_delivery d ON d.id=o.flow_delivery_id WHERE d.bot_id=:bot AND o.created_at>=:since AND o.status='failed' GROUP BY o.error",
                "last_success": "SELECT max(o.sent_at) FROM delivery_operation o JOIN flow_delivery d ON d.id=o.flow_delivery_id WHERE d.bot_id=:bot AND o.status='sent'",
                "published_flows": "SELECT f.id, f.kind, f.assignment_mode FROM content_flow f JOIN content_flow_version v ON v.flow_id=f.id AND v.status='published' WHERE f.bot_id=:bot AND f.is_active",
                "selected_assignments": "SELECT a.flow_id, a.channel_id FROM flow_channel_assignment a JOIN content_flow f ON f.id=a.flow_id WHERE f.bot_id=:bot AND f.is_active",
            }.items():
                rows = (await session.execute(text(sql), parameters)).all()
                summary[name] = [list(row) for row in rows]
            summary["operation_errors"] = [[safe_reason(reason), count] for reason, count in summary["operation_errors"]]
            summaries[managed.id] = summary
    # No DB transaction held during network requests; bounded requests, serial probes.
    for managed in bots:
        try:
            token = keyring.decrypt(managed.token_ciphertext)
            async with Bot(token, session=AiohttpSession(timeout=10)) as bot:
                result = await inspect_telegram(bot, managed, [c for c in channels if c.bot_id == managed.id],
                                                settings.public_webhook_base_url)
        except Exception as exc:
            result = {"bot_id": managed.id, "error": normalize_delivery_error(exc)}
        print(json.dumps({**result, "since": since.isoformat(), "database": summaries[managed.id]}, default=str), flush=True)


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--bot-id", type=int)
    args = parser.parse_args()
    if not 1 <= args.hours <= 720:
        parser.error("--hours must be between 1 and 720")
    asyncio.run(audit(hours=args.hours, bot_id=args.bot_id))


if __name__ == "__main__":
    run()
