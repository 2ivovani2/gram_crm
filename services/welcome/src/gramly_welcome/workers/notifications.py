from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, MessageEntity
from prometheus_client import start_http_server

from ..bot_ui import inline_button, load_bot_ui_theme, plain_markup
from ..config import get_settings
from ..db import session_factory
from ..learning import claim_notification, finish_notification
from ..metrics import OWNER_NOTIFICATIONS, WORKER_ACTIVE
from ..owner_bot import interface_bot

logger = logging.getLogger(__name__)
WORKER_ID = f"{os.getenv('HOSTNAME', 'local')}:{uuid.uuid4().hex[:8]}"


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def notification_text_and_entities(payload: dict[str, Any]) -> tuple[str, list[MessageEntity]]:
    title = str(payload.get("title") or "GramlyHello")
    body = str(payload.get("body") or "")
    prefix = f"{title}\n\n"
    entities = [MessageEntity(type="bold", offset=0, length=_utf16_length(title))]
    shift = _utf16_length(prefix)
    for raw in payload.get("entities") or []:
        try:
            entity = MessageEntity.model_validate(raw)
        except (TypeError, ValueError):
            continue
        entities.append(entity.model_copy(update={"offset": entity.offset + shift}))
    return prefix + body, entities


async def process_one() -> bool:
    settings = get_settings()
    async with session_factory() as session:
        claimed = await claim_notification(
            session,
            WORKER_ID,
            lease_seconds=settings.lease_seconds,
        )
    if claimed is None:
        return False
    notification, telegram_id = claimed
    payload = dict(notification.payload)
    text, entities = notification_text_and_entities(payload)
    markup: InlineKeyboardMarkup | None = None
    async with session_factory() as session:
        theme = await load_bot_ui_theme(session)
    button_text = str(payload.get("button_text") or "")
    button_url = str(payload.get("button_url") or "")
    if button_text and button_url:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    inline_button(
                        button_text,
                        url=button_url,
                        style="primary",
                        emoji_key="guide",
                        theme=theme,
                    )
                ]
            ]
        )
    error: str | None = None
    try:
        try:
            await interface_bot().send_message(
                telegram_id,
                text,
                entities=entities,
                parse_mode=None,
                reply_markup=markup,
            )
        except TelegramBadRequest:
            if markup is None:
                raise
            fallback_entities = [entity for entity in entities if entity.type != "custom_emoji"]
            await interface_bot().send_message(
                telegram_id,
                text,
                entities=fallback_entities,
                parse_mode=None,
                reply_markup=plain_markup(markup),
            )
    except TelegramAPIError as exc:
        error = str(exc)
        OWNER_NOTIFICATIONS.labels(notification.kind, "retry").inc()
        logger.warning(
            "Owner notification delivery failed notification_id=%s kind=%s",
            notification.id,
            notification.kind,
        )
    else:
        OWNER_NOTIFICATIONS.labels(notification.kind, "sent").inc()
    async with session_factory() as session:
        await finish_notification(
            session,
            notification.id,
            error=error,
            max_attempts=settings.max_attempts,
        )
    return True


async def worker_loop() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gramly Welcome notification worker")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for event_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(event_signal, stopping.set)
    WORKER_ACTIVE.labels("notifications").inc()
    try:
        while not stopping.is_set():
            if await process_one():
                continue
            try:
                await asyncio.wait_for(stopping.wait(), timeout=1)
            except TimeoutError:
                pass
    finally:
        WORKER_ACTIVE.labels("notifications").dec()


def run() -> None:
    start_http_server(9090)
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
