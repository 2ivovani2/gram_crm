"""
DEPRECATED: daily client data entry has moved to the web UI at /stats/clients/.
This file is kept as a stub so any stale FSM state (AdminDailyReportState)
is handled gracefully — the handler simply redirects to main menu.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_bot.admin_keyboards import get_admin_main_menu
from apps.telegram_bot.callbacks import AdminMenuCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminDailyReportState

router = Router(name="admin_daily")


@router.callback_query(AdminMenuCallback.filter(F.section == "daily"), IsAdmin())
async def cb_daily_section(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await safe_edit_text(
        callback,
        "ℹ️ Ввод данных перенесён в веб-интерфейс.\n\n"
        "Используйте <b>/stats/clients/</b> для управления клиентами и счётчиками.",
        get_admin_main_menu(),
    )


# Clear stale FSM states so users are never stuck
@router.message(AdminDailyReportState.waiting_for_link, IsAdmin())
@router.message(AdminDailyReportState.waiting_for_client_nick, IsAdmin())
@router.message(AdminDailyReportState.waiting_for_client_rate, IsAdmin())
@router.message(AdminDailyReportState.waiting_for_total_applications, IsAdmin())
async def handle_stale_daily_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "⚠️ Эта форма устарела. Ввод данных перенесён в /stats/clients/",
        reply_markup=get_admin_main_menu(),
    )
