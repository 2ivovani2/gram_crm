"""
Worker: withdrawal request flow.

Flow:
  /start → main menu → "💸 Вывод средств"
  → choose method (CryptoBot / USDT TRC20)
  → enter details (username / wallet)
  → saved + confirmation message sent
  → all admins notified
"""
import re
from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import WorkerCallback, WorkerWithdrawalCallback
from apps.telegram_bot.keyboards import (
    get_withdrawal_method_keyboard, get_cancel_keyboard,
    get_back_to_start_keyboard,
)
from apps.telegram_bot.permissions import IsActivatedWorker
from apps.telegram_bot.services import answer_and_edit
from apps.telegram_bot.states import WorkerWithdrawalState
from apps.users.models import User

router = Router(name="worker_withdrawal")

_TRC20_RE = re.compile(r'^T[a-zA-Z0-9]{33}$')
_USERNAME_RE = re.compile(r'^@?[a-zA-Z0-9_]{3,32}$')


# ── Entry point ───────────────────────────────────────────────────────────────

_MIN_WITHDRAWAL = Decimal("700")


@router.callback_query(WorkerCallback.filter(F.action == "withdrawal"), IsActivatedWorker())
async def cb_withdrawal_start(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if db_user.balance <= 0:
        await callback.answer("Недостаточно средств для вывода.", show_alert=True)
        return
    if db_user.balance < _MIN_WITHDRAWAL:
        await callback.answer(
            f"Минимальная сумма вывода {_MIN_WITHDRAWAL:.0f} ₽. "
            f"Ваш баланс: {db_user.balance:.2f} ₽.",
            show_alert=True,
        )
        return
    await callback.answer()
    await state.set_state(WorkerWithdrawalState.choosing_method)
    await answer_and_edit(
        callback,
        f"💸 <b>Вывод средств</b>\n\n"
        f"Доступно: <b>{db_user.balance:.2f} ₽</b>\n\n"
        "Выберите удобный способ выплаты:",
        get_withdrawal_method_keyboard(),
    )


# ── Method selection ──────────────────────────────────────────────────────────

@router.callback_query(WorkerWithdrawalCallback.filter(F.action == "method_cryptobot"), WorkerWithdrawalState.choosing_method)
async def cb_method_cryptobot(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.update_data(method="cryptobot")
    await state.set_state(WorkerWithdrawalState.entering_details)
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>CryptoBot</b>\n\n"
        "Укажите @username, куда производить выплату через CryptoBot:\n"
        "<i>Пример: @username</i>",
        reply_markup=get_cancel_keyboard(),
    )


@router.callback_query(WorkerWithdrawalCallback.filter(F.action == "method_usdt"), WorkerWithdrawalState.choosing_method)
async def cb_method_usdt(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.update_data(method="usdt_trc20")
    await state.set_state(WorkerWithdrawalState.entering_details)
    await callback.answer()
    await callback.message.edit_text(
        "💎 <b>USDT TRC20</b>\n\n"
        "Укажите адрес кошелька USDT (TRC20):\n"
        "<i>Пример: TRxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</i>",
        reply_markup=get_cancel_keyboard(),
    )


# ── Details input ─────────────────────────────────────────────────────────────

@router.message(WorkerWithdrawalState.entering_details, IsActivatedWorker())
async def process_withdrawal_details(message: Message, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    method = data["method"]
    raw = (message.text or "").strip()

    # Validate
    if method == "cryptobot":
        username = raw.lstrip("@")
        if not _USERNAME_RE.match(raw):
            await message.answer(
                "⚠️ Неверный формат. Введите @username (от 3 до 32 символов, только буквы/цифры/_).",
                reply_markup=get_cancel_keyboard(),
            )
            return
        details = f"@{username}"
    else:  # usdt_trc20
        if not _TRC20_RE.match(raw):
            await message.answer(
                "⚠️ Неверный адрес TRC20. Адрес должен начинаться с T и содержать 34 символа.\n"
                "<i>Пример: TRxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</i>",
                reply_markup=get_cancel_keyboard(),
            )
            return
        details = raw

    await state.clear()

    # Re-fetch balance (may have changed)
    from apps.users.services import UserService
    db_user = await sync_to_async(UserService.get_by_telegram_id)(db_user.telegram_id)
    if db_user.balance <= 0:
        await message.answer("Баланс обнулился, вывод невозможен.", reply_markup=get_back_to_start_keyboard())
        return

    amount = db_user.balance
    method_label = "CryptoBot (@cryptobot)" if method == "cryptobot" else "USDT TRC20"

    # Save withdrawal request
    from apps.withdrawals.services import WithdrawalService
    withdrawal = await sync_to_async(WithdrawalService.create)(db_user, amount, method, details)

    # Confirm to user
    await message.answer(
        f"✅ <b>Заявка на вывод успешно создана!</b>\n\n"
        f"Сумма: <b>{amount:.2f} ₽</b>\n"
        f"Способ: <b>{method_label}</b>\n"
        f"Реквизиты: <code>{details}</code>\n"
        f"Статус: <b>Ожидает обработки</b>",
        reply_markup=get_back_to_start_keyboard(),
    )

    # Notify all admins
    await _notify_admins(withdrawal, db_user, amount, method_label, details)


async def _notify_admins(withdrawal, user: User, amount, method_label: str, details: str) -> None:
    from apps.users.models import User as UserModel
    from apps.telegram_bot.admin_keyboards import get_withdrawal_admin_notify_keyboard
    from apps.telegram_bot.bot import get_bot
    from apps.withdrawals.services import WithdrawalService

    bot = get_bot()
    admins = await sync_to_async(
        lambda: list(UserModel.objects.filter(role="admin", is_blocked_bot=False).values_list("telegram_id", flat=True))
    )()

    text = (
        f"💸 <b>Новая заявка на вывод #{withdrawal.pk}</b>\n\n"
        f"👤 Пользователь: <b>{user.display_name}</b>\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n\n"
        f"Сумма: <b>{amount:.2f} ₽</b>\n"
        f"Способ: <b>{method_label}</b>\n"
        f"Реквизиты: <code>{details}</code>"
    )
    keyboard = get_withdrawal_admin_notify_keyboard(withdrawal.id)

    notifications = []
    for admin_tg_id in admins:
        try:
            msg = await bot.send_message(admin_tg_id, text, reply_markup=keyboard)
            notifications.append({"telegram_id": admin_tg_id, "message_id": msg.message_id})
        except Exception:
            pass

    if notifications:
        await sync_to_async(WithdrawalService.save_admin_notifications)(withdrawal, notifications)
