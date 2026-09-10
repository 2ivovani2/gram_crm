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


logger = logging.getLogger(__name__)
router = Router(name="control_invite")


def respond_to_invite(invite_id, user, *, accept):
    """Enrollment is allowed before personal.use exists; it cannot restore revoked access."""
    from django.core.exceptions import PermissionDenied
    from django.db import transaction
    from django.utils import timezone
    from apps.control.models import WorkerInvite, InviteStatus
    from apps.crm.provisioning import ensure_default_workspace_membership
    from apps.users.models import User, UserRole, UserStatus

    with transaction.atomic():
        fresh = User.objects.select_for_update().get(pk=user.pk)
        if not fresh.is_active or fresh.status in {UserStatus.INACTIVE, UserStatus.BANNED}:
            raise PermissionDenied("Доступ отозван. Обратитесь к администратору.")
        invite = WorkerInvite.objects.select_for_update(of=("self",)).select_related("invited_by").filter(
            pk=invite_id, user_id=fresh.pk,
        ).first()
        if invite is None:
            raise ValueError("Приглашение не найдено.")
        if invite.status != InviteStatus.PENDING:
            raise ValueError("Вы уже ответили на это приглашение.")
        invite.status = InviteStatus.ACCEPTED if accept else InviteStatus.DECLINED
        invite.responded_at = timezone.now()
        invite.save(update_fields=["status", "responded_at"])
        if accept:
            # A role assigned in CRM after the invitation must not be downgraded.
            if fresh.role == UserRole.ANONYMOUS:
                fresh.role = UserRole.WORKER
            fresh.status = UserStatus.ACTIVE
            fresh.is_activated = True
            fresh.save(update_fields=["role", "status", "is_activated", "updated_at"])
            ensure_default_workspace_membership(fresh)
        invite.user = fresh
        return invite


def _make_bot():
    """Fresh Bot instance for asyncio.run() calls — avoids event-loop conflicts with singleton."""
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from django.conf import settings
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


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

async def _notify_admins(bot, text: str) -> None:
    from apps.users.models import User, UserRole
    from asgiref.sync import sync_to_async

    admin_ids = await sync_to_async(
        lambda: list(User.objects.filter(role=UserRole.ADMIN, is_blocked_bot=False)
                     .values_list("telegram_id", flat=True))
    )()
    from apps.telegram_bot.permissions import permitted_recipient_ids
    admin_ids = await permitted_recipient_ids(admin_ids, "access.manage")
    for tg_id in admin_ids:
        try:
            await bot.send_message(tg_id, text)
        except Exception as exc:
            logger.warning("invite: admin notify failed tg_id=%s: %s", tg_id, exc)


# ── Bot callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(WorkerInviteCB.filter(F.action == "accept"))
async def cb_invite_accept(callback: CallbackQuery, callback_data: WorkerInviteCB, db_user):
    from asgiref.sync import sync_to_async
    try:
        invite = await sync_to_async(respond_to_invite)(callback_data.invite_id, db_user, accept=True)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    user = invite.user

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
        callback.bot,
        f"✅ <b>Сотрудник принял приглашение</b>\n\n"
        f"👤 {user_name}\n"
        f"📨 Пригласил: {inviter_name}",
    )


@router.callback_query(WorkerInviteCB.filter(F.action == "decline"))
async def cb_invite_decline(callback: CallbackQuery, callback_data: WorkerInviteCB, db_user):
    from asgiref.sync import sync_to_async
    try:
        invite = await sync_to_async(respond_to_invite)(callback_data.invite_id, db_user, accept=False)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

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
        callback.bot,
        f"❌ <b>Сотрудник отклонил приглашение</b>\n\n"
        f"👤 {user_name}\n"
        f"📨 Пригласил: {inviter_name}",
    )


# ── Sync send (called from CRM view via asyncio.run) ──────────────────────────

def send_worker_invite_sync(invite_id: int, user_tg_id: int, inviter_name: str) -> None:
    """Send invite message to user and save bot_message_id. Fails silently."""

    async def _send():
        from apps.control.models import WorkerInvite
        from asgiref.sync import sync_to_async

        bot = _make_bot()
        try:
            msg = await bot.send_message(
                user_tg_id,
                f"🤝 <b>Вас приглашают в команду Gramly!</b>\n\n"
                f"Администратор <b>{inviter_name}</b> предлагает вам стать сотрудником.\n\n"
                "Примите приглашение чтобы получить доступ к личному кабинету.",
                reply_markup=_invite_keyboard(invite_id),
            )
            await sync_to_async(
                lambda: WorkerInvite.objects.filter(pk=invite_id)
                        .update(bot_message_id=msg.message_id)
            )()
            logger.info("send_worker_invite_sync: sent to tg_id=%s invite=%s msg=%s",
                        user_tg_id, invite_id, msg.message_id)
        except Exception as exc:
            logger.error("send_worker_invite_sync: failed tg_id=%s invite=%s: %s",
                         user_tg_id, invite_id, exc)
        finally:
            await bot.session.close()

    try:
        asyncio.run(_send())
    except Exception as exc:
        logger.error("send_worker_invite_sync: asyncio.run failed: %s", exc)
