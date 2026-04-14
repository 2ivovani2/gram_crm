"""Worker and Curator inline keyboards."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .callbacks import WorkerCallback, WorkerWithdrawalCallback, CuratorCallback


def _btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=WorkerCallback(action=action).pack())


def _cur_btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CuratorCallback(action=action).pack())


def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


# ── Worker menus ──────────────────────────────────────────────────────────────

def get_main_menu_keyboard(is_activated: bool = True, channels_db_url: str = "") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("👤 Личный кабинет", "profile"))
    b.row(_btn("🤝 Мои рефералы", "referrals"), _btn("📊 Статистика", "stats"))
    b.row(_btn("💸 Вывод средств", "withdrawal"))
    if channels_db_url:
        b.row(_url_btn("📂 База каналов", channels_db_url))
    return b.as_markup()


def get_profile_keyboard(channels_db_url: str = "") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_btn("🤝 Рефералы", "referrals"), _btn("📊 Статистика", "stats"))
    b.row(_btn("💸 Вывод средств", "withdrawal"))
    if channels_db_url:
        b.row(_url_btn("📂 База каналов", channels_db_url))
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


# ── Curator menus ─────────────────────────────────────────────────────────────

def get_curator_main_menu_keyboard(channels_db_url: str = "") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_cur_btn("👥 Мои рефералы", "referrals"))
    b.row(_cur_btn("📊 Статистика", "stats"))
    # Withdrawal uses WorkerCallback — handled by worker/withdrawal.py with IsActivatedWorker()
    # which accepts curators too
    b.row(_btn("💸 Вывод средств", "withdrawal"))
    if channels_db_url:
        b.row(_url_btn("📂 База каналов", channels_db_url))
    return b.as_markup()


def get_curator_back_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_cur_btn("🔙 На главную", "back_to_main"))
    return b.as_markup()


def get_curator_cancel_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(_cur_btn("❌ Отмена", "cancel"))
    return b.as_markup()
