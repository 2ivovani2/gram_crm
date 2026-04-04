"""Worker-facing inline keyboards."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .callbacks import WorkerCallback, WorkerWithdrawalCallback


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=WorkerCallback(action=action).pack())


def get_main_menu_keyboard(is_activated: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_activated:
        b.row(_btn("👤 Личный кабинет", "profile"))
        b.row(_btn("🤝 Мои рефералы", "referrals"), _btn("📊 Статистика", "stats"))
        b.row(_btn("💸 Вывод средств", "withdrawal"))
    else:
        b.row(_btn("🔑 Ввести invite key", "enter_invite"))
    return b.as_markup()


def get_profile_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🤝 Рефералы", "referrals"), _btn("📊 Статистика", "stats"))
    b.row(_btn("💸 Вывод средств", "withdrawal"))
    b.row(_btn("🔙 На главную", "back_to_start"))
    return b.as_markup()


def get_withdrawal_method_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🤖 CryptoBot", callback_data=WorkerWithdrawalCallback(action="method_cryptobot").pack()),
        InlineKeyboardButton(text="💎 USDT TRC20", callback_data=WorkerWithdrawalCallback(action="method_usdt").pack()),
    )
    b.row(_btn("❌ Отмена", "cancel"))
    return b.as_markup()


def get_back_to_start_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🔙 На главную", "back_to_start"))
    return b.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("❌ Отмена", "cancel"))
    return b.as_markup()
