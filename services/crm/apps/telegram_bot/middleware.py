from __future__ import annotations
import logging
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """Check a fresh grant before callbacks and each FSM message, including stale buttons."""

    async def __call__(self, handler, event, data):
        from django.core.exceptions import PermissionDenied
        from apps.telegram_bot.permissions import callback_permissions, state_permissions, get_bot_access

        required = None
        if isinstance(event, CallbackQuery):
            required = callback_permissions(event.data or "")
        elif isinstance(event, Message):
            words = (event.text or "").split(maxsplit=1)
            command = words[0].split("@", 1)[0] if words else ""
            if command == "/accounting":
                required = ("withdrawals.view",)
            elif command == "/admin":
                required = callback_permissions("adm:main")
            elif command != "/start":
                state = data.get("state")
                raw = await state.get_state() if state is not None else data.get("raw_state")
                required = state_permissions(raw)
        if required is not None:
            access = await get_bot_access(data.get("db_user"))
            if not any(access.allows(code) for code in required):
                return await self._deny(event, data)
        try:
            return await handler(event, data)
        except PermissionDenied:
            # The service rechecks grants under its transaction lock. A revocation
            # between this adapter and the service is an ordinary denied action.
            return await self._deny(event, data)

    @staticmethod
    async def _deny(event, data):
        state = data.get("state")
        if state is not None:
            await state.clear()
        text = "Доступ к этому действию отозван или не выдан. Откройте /start для актуального меню."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None


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
