"""Configure the distinct GramlyView Telegram bot after its secret is mounted."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.types import BotCommand

from .config import get_settings

logger = logging.getLogger(__name__)


async def _set_profile_photo(token: str) -> None:
    avatar = Path("/app/assets/gramlyview-avatar.png")
    if not avatar.exists():
        logger.warning("GramlyView avatar asset is missing")
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
        logger.warning("Could not update GramlyView avatar", exc_info=True)
        return
    if not response.is_success or not payload.get("ok"):
        logger.warning("Telegram rejected GramlyView avatar: %s", payload.get("description"))


async def reconcile() -> None:
    settings = get_settings()
    if not settings.view_bot_token or not settings.view_webhook_secret or not settings.view_bot_username:
        raise RuntimeError("View bot token, username and webhook secret are required")
    async with Bot(settings.view_bot_token) as bot:
        identity = await bot.get_me()
        if (identity.username or "").lower() != settings.view_bot_username.lstrip("@").lower():
            raise RuntimeError("Configured GramlyView username does not match token")
        await bot.set_my_name(name="GramlyView")
        await bot.set_my_short_description(
            short_description="Аналитика Telegram-каналов с проверенными данными."
        )
        await bot.set_my_description(
            description=(
                "Отправьте ссылку на Telegram-канал, публикацию или список каналов. "
                "GramlyView покажет доступные показатели и честно отметит недоступные. "
                "Бесплатно для пользователей Gramly."
            )
        )
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Открыть GramlyView"),
            ]
        )
        await bot.set_webhook(
            f"{settings.public_service_base_url.rstrip('/')}/view/webhook/",
            ip_address=settings.webhook_ip_address or None,
            secret_token=settings.view_webhook_secret,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
            max_connections=20,
        )
    await _set_profile_photo(settings.view_bot_token)


def run() -> None:
    asyncio.run(reconcile())
