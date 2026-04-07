"""Curator: referral list view."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import CuratorCallback
from apps.telegram_bot.keyboards import get_curator_back_keyboard
from apps.telegram_bot.permissions import IsCurator
from apps.telegram_bot.services import safe_edit_text
from apps.users.models import User

router = Router(name="curator_referrals")


@router.callback_query(CuratorCallback.filter(F.action == "referrals"), IsCurator())
async def cb_curator_referrals(callback: CallbackQuery, db_user: User) -> None:
    from apps.referrals.services import ReferralService
    from decimal import Decimal

    referral_count, recent_referrals, referral_url = await sync_to_async(
        lambda: (
            db_user.referrals.count(),
            list(db_user.referrals.only("first_name", "telegram_username", "telegram_id", "attracted_count", "created_at").order_by("-created_at")[:10]),
            ReferralService.get_referral_url(db_user),
        )
    )()

    total_attracted = sum(r.attracted_count for r in recent_referrals)

    lines = [
        f"👥 <b>Ваши рефералы</b> ({referral_count} всего)",
        "",
        f"🤝 Ставка за реферала: <b>{db_user.referral_rate:.2f} руб./чел.</b>",
        "",
        "🔗 <b>Ваша реферальная ссылка:</b>",
        f"<code>{referral_url}</code>",
        "",
    ]

    if not recent_referrals:
        lines.append("У вас пока нет рефералов. Поделитесь ссылкой!")
    else:
        lines.append(f"📝 Всего заявок от ваших рефералов: <b>{total_attracted}</b>")
        lines.append("")
        for i, ref in enumerate(recent_referrals, 1):
            name = ref.display_name if hasattr(ref, "display_name") else ref.first_name or str(ref.telegram_id)
            lines.append(
                f"{i}. <b>{name}</b> — заявок: {ref.attracted_count} | {ref.created_at.strftime('%d.%m.%Y')}"
            )
        if referral_count > 10:
            lines.append(f"\n<i>...и ещё {referral_count - 10}</i>")

    await callback.answer()
    await safe_edit_text(callback, "\n".join(lines), get_curator_back_keyboard())
