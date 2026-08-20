from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from .config import get_settings

logger = logging.getLogger(__name__)


async def _set_profile_photo(token: str) -> None:
    avatar = Path("/app/assets/gramlyhello-avatar.png")
    if not avatar.exists():
        logger.warning("GramlyHello profile avatar is missing: %s", avatar)
        return
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/setMyProfilePhoto",
                data={"photo": json.dumps({"type": "static", "photo": "attach://avatar"})},
                files={"avatar": (avatar.name, avatar.read_bytes(), "image/png")},
            )
            payload = response.json()
    except (httpx.HTTPError, ValueError, OSError):
        logger.warning("Could not update GramlyHello profile avatar", exc_info=True)
        return
    if not response.is_success or not payload.get("ok"):
        logger.warning("Telegram rejected GramlyHello profile avatar: %s", payload.get("description"))


async def reconcile() -> None:
    settings = get_settings()
    if not settings.interface_bot_token or not settings.interface_webhook_secret:
        raise RuntimeError("Interface bot token and webhook secret are required")
    async with Bot(settings.interface_bot_token) as bot:
        await bot.set_my_name(name="GramlyHello")
        await bot.set_my_short_description(
            short_description="Умные приветствия, цепочки и автоматизация для Telegram-сообществ."
        )
        await bot.set_my_description(
            description=(
                "GramlyHello встречает новых участников от имени вашего бота: отправляет "
                "персональные цепочки с медиа и кнопками, принимает заявки, показывает "
                "честную статистику и помогает развивать Telegram-сообщество.\n\n"
                "Нажмите «Запустить», чтобы подключить первого бота."
            )
        )
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Открыть главное меню"),
                BotCommand(command="help", description="Инструкции и советы"),
            ]
        )
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
    await _set_profile_photo(settings.interface_bot_token)


def run() -> None:
    asyncio.run(reconcile())
