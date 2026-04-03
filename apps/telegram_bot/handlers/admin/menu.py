"""Admin: main menu entry point."""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_bot.admin_keyboards import get_admin_main_menu
from apps.telegram_bot.callbacks import AdminMenuCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.users.models import User

router = Router(name="admin_menu")


def _admin_menu_text(db_user: User) -> str:
    return (
        f"🛠 <b>Admin Panel</b>\n\n"
        f"Привет, <b>{db_user.display_name}</b>!\n"
        "Выберите раздел:"
    )


async def send_admin_main_menu(event: Message | CallbackQuery, db_user: User) -> None:
    """Helper called from other places (e.g. /start redirect for admins)."""
    text = _admin_menu_text(db_user)
    markup = get_admin_main_menu()
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await safe_edit_text(event, text, markup)


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await send_admin_main_menu(message, db_user)


@router.callback_query(AdminMenuCallback.filter(F.section == "main"), IsAdmin())
async def cb_admin_main(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await safe_edit_text(callback, _admin_menu_text(db_user), get_admin_main_menu())
