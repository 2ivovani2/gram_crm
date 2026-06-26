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

    if db_user.is_accountant():
        await message.answer(
            "💼 <b>Панель бухгалтера</b>\n\nВыберите раздел:",
            reply_markup=__accountant_menu(),
        )
        return

    if db_user.is_worker() and db_user.status == "active":
        from apps.control.bot.worker_handlers import send_worker_cabinet
        await send_worker_cabinet(message, db_user, state)
        return

    # Anonymous / unregistered users
    await message.answer(
        f"👋 Привет, <b>{db_user.display_name}</b>!\n\n"
        "Вы не зарегистрированы как сотрудник.\n"
        "Если вы работаете с нами — обратитесь к администратору.",
    )


def __accountant_menu():
    from apps.control.bot.keyboards import accountant_main_menu
    return accountant_main_menu()
