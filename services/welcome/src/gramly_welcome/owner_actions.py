"""Commit owner mutations, FSM state and response intent as one unit of work.

Telegram screen delivery runs AFTER the transaction and can be retried without
re-entering business handlers. External provider operations still rely on their
existing provider idempotency (not on replaying arbitrary Telegram callbacks).
"""

from __future__ import annotations

import copy
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from .db import independent_session, owner_transaction
from .models import OwnerAction, OwnerControlState, OwnerReply


@dataclass
class ActionContext:
    update_id: int
    state: OwnerControlState
    values: dict[str, Any] = field(default_factory=dict)
    screen: dict[str, Any] | None = None


current_action: ContextVar[ActionContext | None] = ContextVar("owner_action", default=None)

ACTOR_LOCK = "hashtextextended('welcome-owner:' || CAST(CAST(:actor AS bigint) AS text), 0)"


class TransactionalRedis:
    """Only owner FSM/UI reads and writes are buffered inside an owner action.

    Locks and operations outside the action keep the normal Redis semantics.
    The database copy is authoritative on every subsequent owner update.
    """

    def __init__(self, redis: Any) -> None:
        self.redis = redis

    def __getattr__(self, name: str) -> Any:
        return getattr(self.redis, name)

    async def get(self, key: str) -> Any:
        context = current_action.get()
        if context is None:
            return await self.redis.get(key)
        if key not in context.values:
            value = await self.redis.get(key)
            context.values[key] = value.decode() if isinstance(value, bytes) else value
        value = context.values[key]
        return value.encode() if isinstance(value, str) else value

    async def set(self, key: str, value: Any, **kwargs: Any) -> Any:
        context = current_action.get()
        if context is None:
            return await self.redis.set(key, value, **kwargs)
        context.values[key] = value.decode() if isinstance(value, bytes) else value
        return True

    async def delete(self, *keys: str) -> Any:
        context = current_action.get()
        if context is None:
            return await self.redis.delete(*keys)
        for key in keys:
            context.values[key] = None
        return len(keys)


async def run_owner_action(payload: dict[str, Any]) -> str:
    from aiogram.dispatcher.event.bases import UNHANDLED
    from aiogram.types import Update

    from .owner_bot import interface_bot, interface_dispatcher

    update = Update.model_validate(payload)
    actor_id = owner_actor_id(payload)
    async with independent_session() as session:
        async with session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '3s'"))
            await session.execute(text("SET LOCAL statement_timeout = '30s'"))
            await session.execute(text(f"SELECT pg_advisory_xact_lock({ACTOR_LOCK})"), {"actor": actor_id})
            await session.execute(
                insert(OwnerControlState).values(actor_id=actor_id).on_conflict_do_nothing()
            )
            state = await session.scalar(
                select(OwnerControlState).where(OwnerControlState.actor_id == actor_id).with_for_update()
            )
            assert state is not None
            if await session.get(OwnerAction, update.update_id) is not None:
                return "replay"
            context = ActionContext(update.update_id, state, copy.deepcopy(state.storage))
            transaction_token = owner_transaction.set(session)
            action_token = current_action.set(context)
            try:
                result = await interface_dispatcher().feed_update(interface_bot(), update)
                outcome = "ignored" if result is UNHANDLED else "applied"
                state.storage = context.values
                session.add(OwnerAction(update_id=update.update_id, actor_id=actor_id, outcome=outcome))
                await session.flush()
                if context.screen is not None:
                    if not context.screen.get("notification"):
                        state.revision += 1
                    session.add(
                        OwnerReply(
                            update_id=update.update_id,
                            actor_id=actor_id,
                            revision=state.revision,
                            payload=context.screen,
                        )
                    )
            finally:
                current_action.reset(action_token)
                owner_transaction.reset(transaction_token)
        # Compatibility mirror only. Failure cannot discard the committed action.
        # New owner updates read the authoritative copy from PostgreSQL.
        storage = interface_dispatcher().storage
        redis = getattr(getattr(storage, "redis", None), "redis", None)
        if redis is not None:
            try:
                for key, value in state.storage.items():
                    if value is None:
                        await redis.delete(key)
                    else:
                        await redis.set(key, value, ex=90 * 86400)
            except Exception:
                pass
    return outcome


def owner_actor_id(payload: dict[str, Any]) -> int:
    for name in ("message", "callback_query", "pre_checkout_query", "chat_member", "my_chat_member"):
        event = payload.get(name)
        sender = event.get("from") if isinstance(event, dict) else None
        if isinstance(sender, dict) and sender.get("id") is not None:
            return int(sender["id"])
    return -int(payload["update_id"])


def stage_screen(payload: dict[str, Any]) -> bool:
    context = current_action.get()
    if context is None:
        return False
    context.screen = payload
    return True
