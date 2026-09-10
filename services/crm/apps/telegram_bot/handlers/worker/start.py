"""Minimal /start handler — registers user in DB, shows CRM/docs links."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apps.users.models import User
from apps.telegram_bot.permissions import bot_can

router = Router(name="start")


def _welcome_keyboard(crm_url: str, docs_url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 Открыть CRM", url=crm_url)
    b.button(text="📖 Документация", url=docs_url)
    b.adjust(1)
    return b.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: User, state: FSMContext) -> None:
    await state.clear()

    # Users with management permissions are handled by the preceding router.
    if await bot_can(db_user, "personal.use"):
        from apps.control.bot.worker_handlers import send_worker_cabinet
        await send_worker_cabinet(message, db_user, state)
        return

    # Anonymous / unregistered users
    await message.answer(
        f"👋 Привет, <b>{db_user.display_name}</b>!\n\n"
        "Рабочий доступ пока не выдан или отозван.\n"
        "Если вы работаете с нами — обратитесь к администратору.",
    )
