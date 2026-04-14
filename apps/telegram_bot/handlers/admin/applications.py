"""
Admin: join request management (replaces invite key system).

Sections:
  adm_a list   → paginated list of PENDING requests
  adm_a view   → individual request card
  adm_a approve→ activate user, notify them, edit admin notifications
  adm_a reject → FSM: enter reason → reject + notify
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import AdminApplicationCallback, AdminMenuCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminApplicationRejectState
from apps.users.models import User

router = Router(name="admin_applications")

PAGE_SIZE = 8


def _list_keyboard(requests, page: int, total: int):
    b = InlineKeyboardBuilder()
    for req in requests:
        u = req.user
        label = u.display_name
        if u.telegram_username:
            label += f" (@{u.telegram_username})"
        b.button(
            text=f"📋 {label}",
            callback_data=AdminApplicationCallback(action="view", request_id=req.pk, page=page),
        )
    b.adjust(1)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    row = []
    if page > 1:
        row.append(AdminApplicationCallback(action="list", page=page - 1))
    if len(row) or page < total_pages:
        from aiogram.types import InlineKeyboardButton
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=AdminApplicationCallback(action="list", page=page - 1).pack()))
        nav.append(InlineKeyboardButton(text=f"· {page}/{total_pages} ·", callback_data=AdminApplicationCallback(action="noop").pack()))
        if page < total_pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=AdminApplicationCallback(action="list", page=page + 1).pack()))
        b.row(*nav)

    from aiogram.types import InlineKeyboardButton
    b.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data=AdminMenuCallback(section="main").pack()))
    return b.as_markup()


def _request_card_keyboard(req):
    b = InlineKeyboardBuilder()
    from apps.users.models import JoinRequestStatus
    if req.status == JoinRequestStatus.PENDING:
        b.button(text="✅ Принять", callback_data=AdminApplicationCallback(action="approve", request_id=req.pk))
        b.button(text="❌ Отклонить", callback_data=AdminApplicationCallback(action="reject_ask", request_id=req.pk))
        b.adjust(2)
    b.row()
    b.button(text="🔙 К списку", callback_data=AdminApplicationCallback(action="list"))
    return b.as_markup()


def _format_request(req) -> str:
    u = req.user
    lines = [
        f"📋 <b>Заявка #{req.pk}</b>\n",
        f"👤 Имя: <b>{u.display_name}</b>",
        f"🆔 Telegram ID: <code>{u.telegram_id}</code>",
    ]
    if u.telegram_username:
        lines.append(f"📱 @{u.telegram_username}")
    if u.referred_by:
        lines.append(f"🤝 Реферал от: <b>{u.referred_by.display_name}</b>")
    if req.message:
        lines.append(f"💬 Сообщение: <i>{req.message[:300]}</i>")
    lines.append(f"\n📅 Подана: {req.created_at.strftime('%d.%m.%Y %H:%M')} МСК")
    lines.append(f"Статус: <b>{req.get_status_display()}</b>")
    if req.reviewed_by:
        lines.append(f"Рассмотрел: {req.reviewed_by.display_name} — {req.reviewed_at.strftime('%d.%m.%Y %H:%M')}")
    return "\n".join(lines)


# ── List ──────────────────────────────────────────────────────────────────────

async def send_applications_list(event: Message | CallbackQuery, page: int = 1) -> None:
    from apps.clients.services import JoinService
    from apps.common.utils import paginate

    pending_qs = await sync_to_async(
        lambda: list(JoinService.get_pending_list().select_related("user", "user__referred_by"))
    )()

    from apps.common.utils import paginate as _pg
    total = len(pending_qs)
    # manual pagination
    start = (page - 1) * PAGE_SIZE
    page_items = pending_qs[start:start + PAGE_SIZE]

    if total == 0:
        text = "📭 <b>Заявок на рассмотрении нет</b>"
    else:
        text = f"📋 <b>Заявки на вступление</b> — {total} шт."

    markup = _list_keyboard(page_items, page, total)

    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await safe_edit_text(event, text, markup)


@router.callback_query(AdminApplicationCallback.filter(F.action == "list"), IsAdmin())
async def cb_list(callback: CallbackQuery, callback_data: AdminApplicationCallback, db_user: User) -> None:
    await callback.answer()
    await send_applications_list(callback, page=callback_data.page)


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminApplicationCallback.filter(F.action == "view"), IsAdmin())
async def cb_view(callback: CallbackQuery, callback_data: AdminApplicationCallback, db_user: User) -> None:
    await callback.answer()
    from apps.users.models import JoinRequest
    req = await sync_to_async(
        lambda: JoinRequest.objects.select_related("user", "user__referred_by", "reviewed_by").get(pk=callback_data.request_id)
    )()
    await safe_edit_text(callback, _format_request(req), _request_card_keyboard(req))


# ── Approve ───────────────────────────────────────────────────────────────────

@router.callback_query(AdminApplicationCallback.filter(F.action == "approve"), IsAdmin())
async def cb_approve(callback: CallbackQuery, callback_data: AdminApplicationCallback, db_user: User) -> None:
    await callback.answer()
    from apps.users.models import JoinRequest
    from apps.clients.services import JoinService, JoinServiceError

    req = await sync_to_async(
        lambda: JoinRequest.objects.select_related("user").get(pk=callback_data.request_id)
    )()
    try:
        await sync_to_async(JoinService.approve)(req, db_user)
    except JoinServiceError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    # Edit the notification message to show decision
    done_text = (
        f"✅ <b>Заявка #{req.pk} принята</b>\n"
        f"👤 {req.user.display_name} — активирован\n"
        f"Рассмотрел: {db_user.display_name}"
    )
    await safe_edit_text(callback, done_text)

    # Notify the worker
    try:
        from django.conf import settings
        channels_url = getattr(settings, "CHANNELS_DB_URL", "")
        from apps.telegram_bot.keyboards import get_main_menu_keyboard
        await callback.bot.send_message(
            req.user.telegram_id,
            f"✅ <b>Ваша заявка принята!</b>\n\nДобро пожаловать, <b>{req.user.display_name}</b>!\nТеперь у вас есть полный доступ.",
            reply_markup=get_main_menu_keyboard(is_activated=True, channels_db_url=channels_url),
        )
    except Exception:
        pass

    # Update other admin notification messages
    await _update_other_admin_notifications(req, done_text, callback.from_user.id, callback.bot)


# ── Reject ────────────────────────────────────────────────────────────────────

@router.callback_query(AdminApplicationCallback.filter(F.action == "reject_ask"), IsAdmin())
async def cb_reject_ask(callback: CallbackQuery, callback_data: AdminApplicationCallback, db_user: User, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminApplicationRejectState.waiting_for_reason)
    await state.update_data(request_id=callback_data.request_id)
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=AdminApplicationCallback(action="view", request_id=callback_data.request_id).pack())
    await callback.message.edit_text(
        "✏️ Введите причину отклонения (или пропустите — отправьте любой символ):",
        reply_markup=b.as_markup(),
    )


@router.message(AdminApplicationRejectState.waiting_for_reason, IsAdmin())
async def process_reject_reason(message: Message, db_user: User, state: FSMContext) -> None:
    data = await state.get_data()
    request_id = data.get("request_id")
    reason = (message.text or "").strip() or "Причина не указана"
    await state.clear()

    from apps.users.models import JoinRequest
    from apps.clients.services import JoinService, JoinServiceError
    req = await sync_to_async(
        lambda: JoinRequest.objects.select_related("user").get(pk=request_id)
    )()
    try:
        await sync_to_async(JoinService.reject)(req, db_user)
    except JoinServiceError as exc:
        await message.answer(f"❌ {exc}")
        return

    done_text = (
        f"❌ <b>Заявка #{req.pk} отклонена</b>\n"
        f"👤 {req.user.display_name}\n"
        f"Причина: {reason}\n"
        f"Рассмотрел: {db_user.display_name}"
    )
    await message.answer(done_text)

    # Notify the worker
    try:
        from aiogram.types import InlineKeyboardButton
        kb = InlineKeyboardBuilder()
        kb.button(text="📝 Подать повторно", callback_data="w:submit_request")
        await message.bot.send_message(
            req.user.telegram_id,
            f"❌ <b>Ваша заявка отклонена</b>\n\nПричина: <i>{reason}</i>\n\nВы можете подать заявку повторно.",
            reply_markup=kb.as_markup(),
        )
    except Exception:
        pass

    await _update_other_admin_notifications(req, done_text, message.from_user.id, message.bot)


# ── Noop ─────────────────────────────────────────────────────────────────────

@router.callback_query(AdminApplicationCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Helper ────────────────────────────────────────────────────────────────────

async def _update_other_admin_notifications(req, done_text: str, acting_admin_tg_id: int, bot) -> None:
    """Edit notification messages sent to other admins to show the decision was made."""
    notifications = req.admin_notifications or []
    for notif in notifications:
        tg_id = notif.get("telegram_id")
        msg_id = notif.get("message_id")
        if tg_id and tg_id != acting_admin_tg_id and msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=tg_id,
                    message_id=msg_id,
                    text=done_text,
                )
            except Exception:
                pass
