from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import CallbackQuery, Message, TelegramObject
from asgiref.sync import sync_to_async
from django.conf import settings

from .services import owner_from_telegram

logger = logging.getLogger(__name__)
_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


class OwnerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user if isinstance(event, (Message, CallbackQuery)) else None
        data["owner"] = await sync_to_async(owner_from_telegram)(user) if user else None
        return await handler(event, data)


def get_interface_bot() -> Bot:
    global _bot
    if _bot is None:
        if not settings.WELCOME_BOT_TOKEN:
            raise RuntimeError("WELCOME_BOT_TOKEN is not configured")
        _bot = Bot(
            settings.WELCOME_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


async def get_interface_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        storage = RedisStorage(aioredis.from_url(settings.REDIS_URL, decode_responses=False))
        dp = Dispatcher(storage=storage)
        middleware = OwnerMiddleware()
        dp.message.outer_middleware(middleware)
        dp.callback_query.outer_middleware(middleware)
        from .handlers import router

        dp.include_router(router)
        _dispatcher = dp
    return _dispatcher
