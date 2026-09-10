"""Accountant-facing handlers for withdrawal processing."""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command

from apps.users.models import User
from apps.telegram_bot.permissions import bot_can, get_bot_access
from apps.control.bot.keyboards import (
    CtrlAccountantCB, accountant_main_menu, accountant_withdrawal_actions,
)

router = Router(name="control_accountant")


@router.message(Command("accounting"))
async def cmd_accounting(message: Message, db_user: User):
    if not await bot_can(db_user, "withdrawals.view"):
        await message.answer("Недостаточно прав. Откройте /start для актуального меню.")
        return
    await message.answer(
        "💼 <b>Панель бухгалтера</b>\n\nВыберите раздел:",
        reply_markup=accountant_main_menu(),
    )


@router.callback_query(CtrlAccountantCB.filter(F.action == "withdrawals"))
async def cb_withdrawals_list(callback: CallbackQuery, db_user: User):
    if not await bot_can(db_user, "withdrawals.view"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    from asgiref.sync import sync_to_async
    from apps.control.services import ControlWithdrawalService
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    withdrawals = await sync_to_async(lambda: list(ControlWithdrawalService.get_pending_list()[:20]))()
    await callback.answer()

    if not withdrawals:
        await callback.message.edit_text(
            "✅ Нет активных заявок на вывод.",
            reply_markup=accountant_main_menu(),
        )
        return

    text = f"💳 <b>Заявки на вывод</b> ({len(withdrawals)}):\n\n"
    b = InlineKeyboardBuilder()
    status_icons = {
        "pending": "🆕", "processing": "🔄", "receipt_sent": "📤",
    }
    for w in withdrawals:
        icon = status_icons.get(w.status, "❓")
        username = f"@{w.user.telegram_username}" if w.user.telegram_username else str(w.user.telegram_id)
        text += f"{icon} #{w.pk} {username} — {w.amount:.2f} ₽\n"
        b.button(
            text=f"#{w.pk} {username} {w.amount:.0f}₽",
            callback_data=CtrlAccountantCB(action="w_view", obj_id=w.pk),
        )

    b.adjust(1)
    await callback.message.edit_text(text, reply_markup=b.as_markup())


@router.callback_query(CtrlAccountantCB.filter(F.action == "w_view"))
async def cb_withdrawal_view(callback: CallbackQuery, callback_data: CtrlAccountantCB, db_user: User):
    if not await bot_can(db_user, "withdrawals.view"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    from asgiref.sync import sync_to_async
    from apps.withdrawals.models import WithdrawalRequest

    w = await sync_to_async(
        lambda: WithdrawalRequest.objects.select_related("user").filter(pk=callback_data.obj_id).first()
    )()
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    username = f"@{w.user.telegram_username}" if w.user.telegram_username else str(w.user.telegram_id)
    text = (
        f"💳 <b>Заявка #{w.pk}</b>\n\n"
        f"Сотрудник: {username}\n"
        f"Сумма: <b>{w.amount:.2f} ₽</b>\n"
        f"Кошелёк TRC20:\n<code>{w.details}</code>\n"
        f"Статус: {w.get_status_display()}\n"
        f"Создана: {w.created_at.strftime('%d.%m.%Y %H:%M')}"
    )

    await callback.answer()
    access = await get_bot_access(db_user)
    can_manage = access.allows("withdrawals.manage") and (
        w.user_id != db_user.pk or access.role == "accountant"
    )
    await callback.message.edit_text(text, reply_markup=accountant_withdrawal_actions(w.pk, can_manage=can_manage))


async def _update_withdrawal(callback: CallbackQuery, callback_data: CtrlAccountantCB,
                              db_user: User, action: str):
    if not await bot_can(db_user, "withdrawals.manage"):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    from asgiref.sync import sync_to_async
    from apps.withdrawals.models import WithdrawalRequest
    from apps.control.services import ControlWithdrawalService

    w = await sync_to_async(
        lambda: WithdrawalRequest.objects.select_related("user").filter(pk=callback_data.obj_id).first()
    )()
    if not w:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Нельзя принять свою же заявку (кроме бухгалтера)
    access = await get_bot_access(db_user)
    if w.user_id == db_user.pk and access.role != "accountant":
        await callback.answer(
            "❌ Нельзя обработать собственную заявку на вывод",
            show_alert=True,
        )
        return

    # The service owns the employee notification and sends it once after commit.
    operations = {
        "processing": (ControlWithdrawalService.mark_processing, "В обработке 🔄"),
        "receipt": (ControlWithdrawalService.mark_receipt_sent, "Чек отправлен 📤"),
        "done": (ControlWithdrawalService.mark_completed, "Выполнена ✅"),
        "reject": (ControlWithdrawalService.reject, "Отклонена ❌"),
    }
    operation, result = operations[action]
    try:
        await sync_to_async(operation)(w, db_user)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    processer = f"@{db_user.telegram_username}" if db_user.telegram_username else str(db_user.telegram_id)

    # Edit/clear other processors' notification messages
    notifications = w.admin_notifications or []
    for notif in notifications:
        if notif.get("telegram_id") == db_user.telegram_id:
            continue
        try:
            await callback.bot.edit_message_text(
                chat_id=notif["telegram_id"],
                message_id=notif["message_id"],
                text=f"💳 Заявка #{w.pk} уже обработана\n{result}\nОбработал: {processer}",
            )
        except Exception:
            pass

    await callback.answer(result)
    await callback.message.edit_text(
        f"Заявка #{w.pk} — {result}\nОбработал: {processer}",
        reply_markup=accountant_main_menu(),
    )


@router.callback_query(CtrlAccountantCB.filter(F.action == "w_processing"))
async def cb_w_processing(callback: CallbackQuery, callback_data: CtrlAccountantCB, db_user: User):
    await _update_withdrawal(callback, callback_data, db_user, "processing")


@router.callback_query(CtrlAccountantCB.filter(F.action == "w_receipt"))
async def cb_w_receipt(callback: CallbackQuery, callback_data: CtrlAccountantCB, db_user: User):
    await _update_withdrawal(callback, callback_data, db_user, "receipt")


@router.callback_query(CtrlAccountantCB.filter(F.action == "w_done"))
async def cb_w_done(callback: CallbackQuery, callback_data: CtrlAccountantCB, db_user: User):
    await _update_withdrawal(callback, callback_data, db_user, "done")


@router.callback_query(CtrlAccountantCB.filter(F.action == "w_reject"))
async def cb_w_reject(callback: CallbackQuery, callback_data: CtrlAccountantCB, db_user: User):
    await _update_withdrawal(callback, callback_data, db_user, "reject")
