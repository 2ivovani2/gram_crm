from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """
    Injects `db_user: User | None` into every handler's data dict.

    - Calls get_or_create_from_telegram for each update
    - Updates last_activity_at
    - Sets db_user=None if from_user is absent (channel posts, etc.)
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = None

        if isinstance(event, Message):
            tg_user = event.from_user
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user

        db_user = None
        if tg_user is not None:
            from apps.users.services import UserService

            db_user, _ = await sync_to_async(UserService.get_or_create_from_telegram)(
                telegram_id=tg_user.id,
                first_name=tg_user.first_name or "",
                last_name=tg_user.last_name,
                telegram_username=tg_user.username,
            )
            await sync_to_async(UserService.update_last_activity)(db_user)

        data["db_user"] = db_user
        return await handler(event, data)
