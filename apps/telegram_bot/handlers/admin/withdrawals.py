"""
Admin: withdrawal request management.
  - list with pagination
  - view card
  - approve (balance deducted, user notified)
  - reject (user notified)
  - after one admin acts: all other admin notification messages are edited to "processed"
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.telegram_bot.admin_keyboards import (
    get_withdrawals_list_keyboard, get_withdrawal_card_keyboard, get_admin_main_menu,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminWithdrawalCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.users.models import User
from apps.common.utils import format_dt

router = Router(name="admin_withdrawals")

_METHOD_LABELS = {"cryptobot": "CryptoBot (@cryptobot)", "usdt_trc20": "USDT TRC20"}
_STATUS_ICONS = {"pending": "⏳", "approved": "✅", "rejected": "❌"}


def _withdrawal_card_text(wd) -> str:
    icon = _STATUS_ICONS.get(wd.status, "❓")
    lines = [
        f"{icon} <b>Заявка на вывод #{wd.pk}</b>",
        "",
        f"👤 Пользователь: <b>{wd.user.display_name}</b>",
        f"🆔 Telegram ID: <code>{wd.user.telegram_id}</code>",
        "",
        f"Сумма: <b>{wd.amount:.2f} ₽</b>",
        f"Способ: <b>{_METHOD_LABELS.get(wd.method, wd.method)}</b>",
        f"Реквизиты: <code>{wd.details}</code>",
        f"Статус: <b>{wd.get_status_display()}</b>",
        "",
        f"Создана: {format_dt(wd.created_at)}",
    ]
    if wd.processed_by:
        lines.append(f"Обработал: {wd.processed_by.display_name} ({format_dt(wd.processed_at)})")
    return "\n".join(lines)


# ── List ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "withdrawals"), IsAdmin())
async def cb_withdrawals_section(callback: CallbackQuery) -> None:
    await callback.answer()
    from apps.withdrawals.services import WithdrawalService
    withdrawals, total = await sync_to_async(WithdrawalService.get_list)(page=1)
    pending = await sync_to_async(
        lambda: __import__("apps.withdrawals.models", fromlist=["WithdrawalRequest"])
        .WithdrawalRequest.objects.filter(status="pending").count()
    )()
    text = f"💸 <b>Заявки на вывод</b>\n\nВсего: {total} | Ожидают: {pending}"
    await safe_edit_text(callback, text, get_withdrawals_list_keyboard(withdrawals, page=1, total=total))


@router.callback_query(AdminWithdrawalCallback.filter(F.action == "list"), IsAdmin())
async def cb_withdrawals_list(callback: CallbackQuery, callback_data: AdminWithdrawalCallback) -> None:
    await callback.answer()
    from apps.withdrawals.services import WithdrawalService
    withdrawals, total = await sync_to_async(WithdrawalService.get_list)(page=callback_data.page)
    text = f"💸 <b>Заявки на вывод</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_withdrawals_list_keyboard(withdrawals, page=callback_data.page, total=total))


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminWithdrawalCallback.filter(F.action == "view"), IsAdmin())
async def cb_withdrawal_view(callback: CallbackQuery, callback_data: AdminWithdrawalCallback) -> None:
    await callback.answer()
    from apps.withdrawals.models import WithdrawalRequest
    wd = await sync_to_async(WithdrawalRequest.objects.select_related("user", "processed_by").get)(pk=callback_data.withdrawal_id)
    await safe_edit_text(callback, _withdrawal_card_text(wd), get_withdrawal_card_keyboard(wd, back_page=callback_data.page))


# ── Approve ───────────────────────────────────────────────────────────────────

@router.callback_query(AdminWithdrawalCallback.filter(F.action == "approve"), IsAdmin())
async def cb_withdrawal_approve(callback: CallbackQuery, callback_data: AdminWithdrawalCallback, db_user: User) -> None:
    from apps.withdrawals.models import WithdrawalRequest
    from apps.withdrawals.services import WithdrawalService

    wd = await sync_to_async(WithdrawalRequest.objects.select_related("user", "processed_by").get)(pk=callback_data.withdrawal_id)

    if wd.status != "pending":
        await callback.answer("Заявка уже обработана.", show_alert=True)
        await _try_edit_processed(callback, wd)
        return

    try:
        wd = await sync_to_async(WithdrawalService.approve)(wd, db_user)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return

    await callback.answer("✅ Заявка исполнена!", show_alert=True)

    # Update this message
    await safe_edit_text(callback, _withdrawal_card_text(wd), get_withdrawal_card_keyboard(wd))

    # Notify user
    await _notify_user(wd, approved=True)

    # Edit other admin notification messages
    await _edit_other_admin_notifications(wd, db_user, approved=True)


# ── Reject ────────────────────────────────────────────────────────────────────

@router.callback_query(AdminWithdrawalCallback.filter(F.action == "reject"), IsAdmin())
async def cb_withdrawal_reject(callback: CallbackQuery, callback_data: AdminWithdrawalCallback, db_user: User) -> None:
    from apps.withdrawals.models import WithdrawalRequest
    from apps.withdrawals.services import WithdrawalService

    wd = await sync_to_async(WithdrawalRequest.objects.select_related("user", "processed_by").get)(pk=callback_data.withdrawal_id)

    if wd.status != "pending":
        await callback.answer("Заявка уже обработана.", show_alert=True)
        await _try_edit_processed(callback, wd)
        return

    try:
        wd = await sync_to_async(WithdrawalService.reject)(wd, db_user)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
        return

    await callback.answer("❌ Заявка отклонена.", show_alert=True)

    await safe_edit_text(callback, _withdrawal_card_text(wd), get_withdrawal_card_keyboard(wd))

    await _notify_user(wd, approved=False)
    await _edit_other_admin_notifications(wd, db_user, approved=False)


# ── Noop ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminWithdrawalCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _try_edit_processed(callback: CallbackQuery, wd) -> None:
    """If already processed, show current state (remove buttons)."""
    try:
        await safe_edit_text(callback, _withdrawal_card_text(wd), get_withdrawal_card_keyboard(wd))
    except Exception:
        pass


async def _notify_user(wd, approved: bool) -> None:
    from apps.telegram_bot.bot import get_bot
    from apps.telegram_bot.keyboards import get_back_to_start_keyboard
    bot = get_bot()
    if approved:
        text = (
            f"✅ <b>Заявка на вывод #{wd.pk} исполнена!</b>\n\n"
            f"Сумма: <b>{wd.amount:.2f} ₽</b>\n"
            f"Реквизиты: <code>{wd.details}</code>\n\n"
            "Средства отправлены. Спасибо!"
        )
    else:
        text = (
            f"❌ <b>Заявка на вывод #{wd.pk} отклонена.</b>\n\n"
            f"Сумма: <b>{wd.amount:.2f} ₽</b>\n\n"
            "Средства возвращены на ваш баланс. Обратитесь к администратору за подробностями."
        )
    try:
        await bot.send_message(wd.user.telegram_id, text, reply_markup=get_back_to_start_keyboard())
    except Exception:
        pass


async def _edit_other_admin_notifications(wd, acting_admin: User, approved: bool) -> None:
    """Edit all other admin notification messages to show 'already processed'."""
    from apps.telegram_bot.bot import get_bot
    bot = get_bot()
    status_word = "✅ Исполнена" if approved else "❌ Отклонена"
    text = (
        f"💸 <b>Заявка #{wd.pk} — {status_word}</b>\n\n"
        f"Обработал: <b>{acting_admin.display_name}</b>\n"
        f"Сумма: {wd.amount:.2f} ₽ | {wd.details}"
    )
    for notif in wd.admin_notifications:
        if notif["telegram_id"] == acting_admin.telegram_id:
            continue  # already updated via safe_edit_text
        try:
            await bot.edit_message_text(
                text,
                chat_id=notif["telegram_id"],
                message_id=notif["message_id"],
            )
        except Exception:
            pass
