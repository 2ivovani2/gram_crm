"""Deliver committed UI intent without ever re-entering business handlers.

The sending marker is committed BEFORE Telegram I/O. After a process/DB failure
an edit can be retried safely; an unacknowledged first send is uncertain, never
blindly repeated. A connection-scoped actor lock spans those short transactions.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .bot_ui import plain_markup, plain_text
from .db import engine
from .metrics import OWNER_REPLIES
from .models import OwnerControlState, OwnerReply
from .owner_actions import ACTOR_LOCK


async def _persist(session: AsyncSession, reply: OwnerReply, **values: Any) -> None:
    async with session.begin():
        for key, value in values.items():
            setattr(reply, key, value)


async def deliver_next_reply(bot: Bot) -> bool:
    now = datetime.now(UTC)
    # Hold the connection explicitly: pool check-in must not leak a session
    # advisory lock to an unrelated request between the two commits.
    async with engine.connect() as connection:
        actor: int | None = None
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                async with session.begin():
                    await session.execute(text("SET LOCAL statement_timeout = '15s'"))
                    due = or_(
                        and_(OwnerReply.status.in_(["pending", "retry"]), OwnerReply.available_at <= now),
                        and_(OwnerReply.status == "sending", OwnerReply.lease_expires_at < now),
                    )
                    first_per_actor = (
                        select(OwnerReply.update_id)
                        .where(due)
                        .distinct(OwnerReply.actor_id)
                        .order_by(OwnerReply.actor_id, OwnerReply.available_at, OwnerReply.update_id)
                    )
                    candidates = (
                        await session.scalars(
                            select(OwnerReply)
                            .where(OwnerReply.update_id.in_(first_per_actor))
                            .order_by(OwnerReply.available_at, OwnerReply.update_id)
                            .with_for_update(skip_locked=True)
                            .limit(16)
                        )
                    ).all()
                    reply = None
                    for candidate in candidates:
                        locked = await session.scalar(
                            text(f"SELECT pg_try_advisory_lock({ACTOR_LOCK})"), {"actor": candidate.actor_id}
                        )
                        if locked:
                            reply = candidate
                            break
                    if reply is None:
                        return False
                    actor = reply.actor_id
                    state = await session.get(OwnerControlState, actor, with_for_update=True)
                    if state is None or (
                        not reply.payload.get("notification") and state.revision != reply.revision
                    ):
                        reply.status = "superseded"
                        OWNER_REPLIES.labels("superseded").inc()
                        return True
                    if reply.status == "sending" and not reply.payload.get("anchor"):
                        reply.status, reply.error = "uncertain", "initial_send_outcome_unknown"
                        OWNER_REPLIES.labels("uncertain").inc()
                        return True
                    payload = reply.payload
                    anchor = payload.get("anchor")
                    reply.status = "sending"
                    reply.attempts += 1
                    reply.lease_expires_at = now + timedelta(seconds=90)
                # No transaction across Telegram I/O. New actions for this actor
                # wait/retry; other actors continue independently.
                markup = InlineKeyboardMarkup.model_validate(payload["markup"])
                status, error = "delivered", ""
                delivery_method = "edit"
                available_at = now
                try:
                    if anchor:
                        try:
                            await bot.edit_message_text(
                                payload["text"],
                                chat_id=payload["chat_id"],
                                message_id=anchor,
                                reply_markup=markup,
                                request_timeout=15,
                            )
                        except TelegramBadRequest as exc:
                            reason = str(exc).lower()
                            if "message is not modified" in reason:
                                pass
                            elif any(
                                x in reason
                                for x in (
                                    "message to edit not found",
                                    "message can't be edited",
                                    "there is no text in the message",
                                )
                            ):
                                anchor = None
                                await _persist(session, reply, payload={**payload, "anchor": None})
                            else:
                                await bot.edit_message_text(
                                    plain_text(payload["text"]),
                                    chat_id=payload["chat_id"],
                                    message_id=anchor,
                                    reply_markup=plain_markup(markup),
                                    request_timeout=15,
                                )
                    if not anchor:
                        delivery_method = "send"
                        try:
                            sent = await bot.send_message(
                                payload["chat_id"], payload["text"], reply_markup=markup, request_timeout=15
                            )
                        except TelegramBadRequest:
                            sent = await bot.send_message(
                                payload["chat_id"],
                                plain_text(payload["text"]),
                                reply_markup=plain_markup(markup),
                                request_timeout=15,
                            )
                        anchor = sent.message_id
                except TelegramRetryAfter as exc:
                    status, error = "retry", "telegram_429"
                    available_at = now + timedelta(seconds=max(1, exc.retry_after))
                except TelegramForbiddenError:
                    status, error = "blocked", "user_blocked_or_forbidden"
                except TelegramBadRequest:
                    status, error = "failed", "invalid_screen"
                except Exception:
                    if not anchor:
                        status, error = "uncertain", "initial_send_outcome_unknown"
                    elif reply.attempts >= 12:
                        status, error = "failed", "telegram_unavailable"
                    else:
                        status, error = "retry", "telegram_unavailable"
                        available_at = now + timedelta(
                            seconds=min(60, 2**reply.attempts) + secrets.randbelow(3)
                        )
                async with session.begin():
                    if status == "delivered" and not payload.get("notification"):
                        values = dict(state.storage)
                        key = payload["storage_key"]
                        screen = json.loads(values.get(key) or "{}")
                        screen["message_id"] = anchor
                        old_anchor = payload.get("replace_anchor")
                        if isinstance(old_anchor, int) and old_anchor != anchor:
                            screen["retired_control_ids"] = list(dict.fromkeys([
                                *screen.get("retired_control_ids", []), old_anchor,
                            ]))
                        values[key] = json.dumps(screen)
                        state.storage = values
                    if status == "delivered":
                        reply.delivered_at = datetime.now(UTC)
                        reply.payload = {**reply.payload, "delivered_message_id": anchor,
                                         "delivery_method": delivery_method}
                    reply.status, reply.error = status, error
                    reply.available_at = available_at
                    reply.lease_expires_at = None
                # The new anchor is durable before deleting the old control.
                # Cleanup failure never retries sendMessage or loses the menu.
                old_anchor = payload.get("replace_anchor")
                if status == "delivered" and isinstance(old_anchor, int) and old_anchor != anchor:
                    try:
                        async with asyncio.timeout(3):
                            await bot.delete_message(payload["chat_id"], old_anchor, request_timeout=3)
                    except Exception:
                        pass  # Persisted retired ID is retried on the next UI interaction.
                    else:
                        async with session.begin():
                            values = dict(state.storage)
                            key = payload["storage_key"]
                            screen = json.loads(values.get(key) or "{}")
                            screen["retired_control_ids"] = [
                                item for item in screen.get("retired_control_ids", []) if item != old_anchor
                            ]
                            values[key] = json.dumps(screen)
                            state.storage = values
                OWNER_REPLIES.labels(status).inc()
                return True
        finally:
            if actor is not None:
                if connection.in_transaction():
                    await connection.rollback()
                await connection.execute(text(f"SELECT pg_advisory_unlock({ACTOR_LOCK})"), {"actor": actor})
                await connection.commit()
