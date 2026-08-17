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


async def _admin_menu_text(db_user: User) -> str:
    from asgiref.sync import sync_to_async
    from apps.control.services import ControlBalanceService
    balance = await sync_to_async(ControlBalanceService.get_available_balance)(db_user)
    return (
        f"🛠 <b>Главное меню</b>\n\n"
        f"Привет, <b>{db_user.display_name}</b>!\n\n"
        f"💰 Баланс: <b>{balance:.2f} ₽</b>"
    )


async def send_admin_main_menu(event: Message | CallbackQuery, db_user: User) -> None:
    text   = await _admin_menu_text(db_user)
    markup = get_admin_main_menu()
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await safe_edit_text(event, text, markup)


@router.message(Command("start", "admin"), IsAdmin())
async def cmd_admin(message: Message, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await send_admin_main_menu(message, db_user)


@router.callback_query(AdminMenuCallback.filter(F.section == "main"), IsAdmin())
async def cb_admin_main(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await send_admin_main_menu(callback, db_user)


@router.callback_query(AdminMenuCallback.filter(F.section == "users"), IsAdmin())
async def cb_menu_users(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    from apps.telegram_bot.handlers.admin.users import send_users_list
    await send_users_list(callback, page=1)
