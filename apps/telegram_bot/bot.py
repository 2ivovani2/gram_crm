"""
Bot and Dispatcher singletons.

get_bot()        — sync, safe to call anywhere
get_dispatcher() — async, initializes Redis FSM storage + registers routers/middleware

Both are lazily initialized and idempotent (safe to call multiple times).
"""
from __future__ import annotations
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        from django.conf import settings
        _bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        logger.info("Telegram Bot initialized")
    return _bot


async def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        import redis.asyncio as aioredis
        from aiogram.fsm.storage.redis import RedisStorage
        from django.conf import settings

        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        storage = RedisStorage(redis=redis_client)
        dp = Dispatcher(storage=storage)

        # ── Middleware ────────────────────────────────────────────────────────
        # Outer middlewares use LIFO ordering: LAST registered runs FIRST.
        #
        # Desired call order:
        #   UserMiddleware (sets db_user) → SubscriptionMiddleware (reads db_user) → Handler
        #
        # To achieve this, register SubscriptionMiddleware FIRST (it becomes
        # the inner wrapper), then UserMiddleware SECOND (it becomes the outer
        # wrapper that executes first in the chain).
        from apps.telegram_bot.middleware import UserMiddleware
        from apps.telegram_bot.subscription import SubscriptionMiddleware

        # Outer middlewares execute in registration order (FIFO).
        # UserMiddleware must run first to inject db_user into data,
        # then SubscriptionMiddleware reads db_user to check channel membership.
        dp.message.outer_middleware(UserMiddleware())
        dp.callback_query.outer_middleware(UserMiddleware())

        dp.message.outer_middleware(SubscriptionMiddleware())
        dp.callback_query.outer_middleware(SubscriptionMiddleware())

        # ── Routers ───────────────────────────────────────────────────────────
        from apps.telegram_bot.router import setup_routers
        setup_routers(dp)

        _dispatcher = dp
        logger.info("aiogram Dispatcher initialized with Redis FSM storage")
    return _dispatcher
