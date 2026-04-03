"""Worker: referral list view."""
from aiogram import Router, F
from aiogram.types import CallbackQuery
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import WorkerCallback
from apps.telegram_bot.keyboards import get_back_to_start_keyboard
from apps.telegram_bot.permissions import IsActivatedWorker
from apps.telegram_bot.services import answer_and_edit
from apps.users.models import User

router = Router(name="worker_invite")


@router.callback_query(WorkerCallback.filter(F.action == "referrals"), IsActivatedWorker())
async def cb_referrals(callback: CallbackQuery, db_user: User) -> None:
    from apps.referrals.services import ReferralService

    referral_count, recent_referrals, referral_url, ref_settings = await sync_to_async(
        lambda: (
            db_user.referrals.count(),
            list(db_user.referrals.order_by("-created_at")[:10]),
            ReferralService.get_referral_url(db_user),
            ReferralService.get_settings(),
        )
    )()

    rate = ref_settings.rate_percent
    rate_line = f"💸 Ваша ставка: <b>{rate}%</b> с заработка реферала\n" if rate > 0 else ""

    if not recent_referrals:
        text = (
            "👥 <b>Ваши рефералы</b>\n\n"
            f"{rate_line}"
            "У вас пока нет рефералов.\n\n"
            "🤝 <b>Ваша реф. ссылка:</b>\n"
            f"<code>{referral_url}</code>\n\n"
            "Поделитесь ссылкой — и новые пользователи будут привязаны к вам."
        )
    else:
        lines = [
            f"👥 <b>Ваши рефералы</b> ({referral_count} всего)\n",
            f"{rate_line}",
            "🤝 <b>Ваша реф. ссылка:</b>",
            f"<code>{referral_url}</code>\n",
        ]
        for i, ref in enumerate(recent_referrals, 1):
            lines.append(f"{i}. {ref.display_name} — {ref.created_at.strftime('%d.%m.%Y')}")
        if referral_count > 10:
            lines.append(f"\n<i>...и ещё {referral_count - 10}</i>")
        text = "\n".join(lines)

    await answer_and_edit(callback, text, get_back_to_start_keyboard())
