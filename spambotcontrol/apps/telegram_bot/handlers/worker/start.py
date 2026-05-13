"""Minimal /start handler — registers user in DB, shows CRM/docs links."""
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apps.users.models import User

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

    if db_user.is_admin():
        from apps.telegram_bot.handlers.admin.menu import send_admin_main_menu
        await send_admin_main_menu(message, db_user)
        return

    from django.conf import settings
    crm_url  = getattr(settings, "CRM_URL",  "https://gramly.tech/crm/login/")
    docs_url = getattr(settings, "DOCS_URL", "https://gramly.tech/docs/")

    await message.answer(
        f"👋 Привет, <b>{db_user.display_name}</b>!\n\n"
        "Добро пожаловать в Gramly.\n"
        "Используй кнопки ниже для доступа к системе.",
        reply_markup=_welcome_keyboard(crm_url, docs_url),
    )
