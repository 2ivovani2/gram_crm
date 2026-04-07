"""Curator: main menu and navigation."""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from apps.telegram_bot.callbacks import CuratorCallback, WorkerCallback
from apps.telegram_bot.keyboards import (
    get_curator_main_menu_keyboard, get_curator_back_keyboard,
)
from apps.telegram_bot.permissions import IsCurator, IsActivatedWorker
from apps.telegram_bot.services import safe_edit_text
from apps.users.models import User

router = Router(name="curator_menu")


async def send_curator_main_menu(event: Message | CallbackQuery, db_user: User) -> None:
    """Send curator main menu. Works for both Message and CallbackQuery."""
    from django.conf import settings
    channels_url = getattr(settings, "CHANNELS_DB_URL", "")
    text = f"👤 Добро пожаловать, <b>{db_user.display_name}</b>!\n\nВы вошли как <b>куратор</b>."
    kb = get_curator_main_menu_keyboard(channels_url)
    if isinstance(event, Message):
        await event.answer(text, reply_markup=kb)
    else:
        await safe_edit_text(event, text, kb)


@router.callback_query(CuratorCallback.filter(F.action == "back_to_main"), IsCurator())
async def cb_back_to_main(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await send_curator_main_menu(callback, db_user)


@router.callback_query(CuratorCallback.filter(F.action == "cancel"), IsCurator())
async def cb_cancel(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    await send_curator_main_menu(callback, db_user)


@router.callback_query(CuratorCallback.filter(F.action == "stats"), IsCurator())
async def cb_curator_stats(callback: CallbackQuery, db_user: User) -> None:
    """Curator stats: balance and referral summary."""
    from asgiref.sync import sync_to_async
    from decimal import Decimal

    db_user = await sync_to_async(
        lambda: type(db_user).objects.select_related().get(pk=db_user.pk)
    )()

    referral_count, total_attracted = await sync_to_async(
        lambda: (
            db_user.referrals.count(),
            sum(r.attracted_count for r in db_user.referrals.only("attracted_count")),
        )
    )()

    referral_earnings = (Decimal(total_attracted) * db_user.referral_rate).quantize(Decimal("0.01"))

    await callback.answer()
    text = (
        "📊 <b>Ваша статистика</b>\n\n"
        f"👥 Рефералов: <b>{referral_count}</b>\n"
        f"📝 Заявок от рефералов: <b>{total_attracted}</b>\n"
        f"🤝 Ваша ставка за реферала: <b>{db_user.referral_rate:.2f} руб./чел.</b>\n"
        f"💰 Заработано от рефералов: <b>{referral_earnings:.2f} ₽</b>\n"
        f"💼 Баланс: <b>{db_user.balance:.2f} ₽</b>"
    )
    await safe_edit_text(callback, text, get_curator_back_keyboard())


# ── Shared withdrawal entry point for curators ────────────────────────────────
# Curator taps "💸 Вывод средств" → uses WorkerCallback(action="withdrawal")
# which is handled in handlers/worker/withdrawal.py with IsActivatedWorker()
# that now also accepts curators — no additional code needed here.
