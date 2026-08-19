from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo

from .config import get_settings


async def reconcile() -> None:
    settings = get_settings()
    if not settings.interface_bot_token or not settings.interface_webhook_secret:
        raise RuntimeError("Interface bot token and webhook secret are required")
    async with Bot(settings.interface_bot_token) as bot:
        await bot.set_webhook(
            f"{settings.public_service_base_url.rstrip('/')}/webhook/",
            secret_token=settings.interface_webhook_secret,
            allowed_updates=[
                "message",
                "callback_query",
                "my_chat_member",
                "pre_checkout_query",
            ],
            drop_pending_updates=False,
            max_connections=40,
        )
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Кабинет",
                web_app=WebAppInfo(url=settings.mini_app_url),
            )
        )


def run() -> None:
    asyncio.run(reconcile())
