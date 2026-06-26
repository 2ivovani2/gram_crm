"""
Worker invite flow — admin sends invite from CRM, user responds in bot.

CRM → send_worker_invite_sync() → bot message with Accept/Decline buttons
User taps Accept → role=worker, notify all admins
User taps Decline → notify all admins
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from apps.telegram_bot.permissions import IsAdmin

logger = logging.getLogger(__name__)
router = Router(name="control_invite")


class WorkerInviteCB(CallbackData, prefix="wi"):
    action: str   # accept | decline
    invite_id: int


def _invite_keyboard(invite_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Принять",
            callback_data=WorkerInviteCB(action="accept", invite_id=invite_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=WorkerInviteCB(action="decline", invite_id=invite_id).pack(),
        ),
    ]])


# ── Async helpers ──────────────────────────────────────────────────────────────

async def _notify_admins(text: str) -> None:
    from apps.telegram_bot.bot import get_bot
    from apps.users.models import User, UserRole
    from asgiref.sync import sync_to_async

    bot = get_bot()
    admin_ids = await sync_to_async(
        lambda: list(User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
                     .values_list("telegram_id", flat=True))
    )()
    for tg_id in admin_ids:
        try:
            await bot.send_message(tg_id, text)
        except Exception as exc:
            logger.warning("invite: admin notify failed tg_id=%s: %s", tg_id, exc)


# ── Bot callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(WorkerInviteCB.filter(F.action == "accept"))
async def cb_invite_accept(callback: CallbackQuery, callback_data: WorkerInviteCB, db_user):
    from asgiref.sync import sync_to_async
    from django.utils import timezone
    from apps.control.models import WorkerInvite, InviteStatus
    from apps.users.models import UserRole

    invite = await sync_to_async(
        lambda: WorkerInvite.objects.select_related("user", "invited_by")
                .filter(pk=callback_data.invite_id).first()
    )()

    if not invite or invite.user_id != db_user.pk:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return

    if invite.status != InviteStatus.PENDING:
        await callback.answer("Вы уже ответили на это приглашение.", show_alert=True)
        return

    # Mark accepted, set worker role
    invite.status = InviteStatus.ACCEPTED
    invite.responded_at = timezone.now()
    await sync_to_async(invite.save)(update_fields=["status", "responded_at"])

    user = invite.user
    user.role = UserRole.WORKER
    await sync_to_async(user.save)(update_fields=["role", "updated_at"])

    await callback.answer("✅ Вы приняли приглашение!")
    await callback.message.edit_text(
        "✅ <b>Вы приняли приглашение!</b>\n\n"
        "Добро пожаловать в команду Gramly.\n"
        "Нажмите /start чтобы открыть личный кабинет."
    )

    # Notify admins
    inviter = invite.invited_by
    inviter_name = f"@{inviter.telegram_username}" if inviter and inviter.telegram_username else "Администратор"
    user_name = f"@{user.telegram_username}" if user.telegram_username else str(user.telegram_id)
    await _notify_admins(
        f"✅ <b>Сотрудник принял приглашение</b>\n\n"
        f"👤 {user_name}\n"
        f"📨 Пригласил: {inviter_name}"
    )


@router.callback_query(WorkerInviteCB.filter(F.action == "decline"))
async def cb_invite_decline(callback: CallbackQuery, callback_data: WorkerInviteCB, db_user):
    from asgiref.sync import sync_to_async
    from django.utils import timezone
    from apps.control.models import WorkerInvite, InviteStatus

    invite = await sync_to_async(
        lambda: WorkerInvite.objects.select_related("user", "invited_by")
                .filter(pk=callback_data.invite_id).first()
    )()

    if not invite or invite.user_id != db_user.pk:
        await callback.answer("Приглашение не найдено.", show_alert=True)
        return

    if invite.status != InviteStatus.PENDING:
        await callback.answer("Вы уже ответили на это приглашение.", show_alert=True)
        return

    invite.status = InviteStatus.DECLINED
    invite.responded_at = timezone.now()
    await sync_to_async(invite.save)(update_fields=["status", "responded_at"])

    await callback.answer("Вы отклонили приглашение.")
    await callback.message.edit_text(
        "❌ <b>Вы отклонили приглашение.</b>\n\n"
        "Если передумаете — свяжитесь с администратором."
    )

    inviter = invite.invited_by
    inviter_name = f"@{inviter.telegram_username}" if inviter and inviter.telegram_username else "Администратор"
    user = invite.user
    user_name = f"@{user.telegram_username}" if user.telegram_username else str(user.telegram_id)
    await _notify_admins(
        f"❌ <b>Сотрудник отклонил приглашение</b>\n\n"
        f"👤 {user_name}\n"
        f"📨 Пригласил: {inviter_name}"
    )


# ── Sync send (called from CRM view via asyncio.run) ──────────────────────────

def send_worker_invite_sync(invite_id: int, user_tg_id: int, inviter_name: str) -> None:
    """Send invite message to user and save bot_message_id. Fails silently."""

    async def _send():
        from apps.telegram_bot.bot import get_bot
        from apps.control.models import WorkerInvite

        bot = get_bot()
        try:
            msg = await bot.send_message(
                user_tg_id,
                f"🤝 <b>Вас приглашают в команду Gramly!</b>\n\n"
                f"Администратор <b>{inviter_name}</b> предлагает вам стать сотрудником.\n\n"
                "Примите приглашение чтобы получить доступ к личному кабинету.",
                reply_markup=_invite_keyboard(invite_id),
            )
            from asgiref.sync import sync_to_async
            await sync_to_async(
                lambda: WorkerInvite.objects.filter(pk=invite_id)
                        .update(bot_message_id=msg.message_id)
            )()
        except Exception as exc:
            logger.error("send_worker_invite_sync: failed tg_id=%s invite=%s: %s",
                         user_tg_id, invite_id, exc)

    try:
        asyncio.run(_send())
    except Exception as exc:
        logger.error("send_worker_invite_sync: asyncio.run failed: %s", exc)
