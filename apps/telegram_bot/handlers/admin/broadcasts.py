"""
Admin: broadcast management.
  - list with pagination
  - create (FSM: title → text → audience → confirm)
  - view card + confirm + launch (with inline confirmation step)
  - delivery log with pagination
"""
from __future__ import annotations
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.broadcasts.models import Broadcast, BroadcastAudience
from apps.broadcasts.services import BroadcastService
from apps.telegram_bot.admin_keyboards import (
    get_broadcasts_list_keyboard, get_broadcast_card_keyboard,
    get_broadcast_launch_confirm_keyboard, get_audience_select_keyboard,
    get_broadcast_delivery_logs_keyboard, get_admin_cancel_keyboard,
    get_admin_main_menu,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminBroadcastCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminBroadcastCreateState
from apps.users.models import User
from apps.common.utils import format_dt, truncate

router = Router(name="admin_broadcasts")

_AUDIENCE_LABELS = {
    BroadcastAudience.ALL: "📢 Всем",
    BroadcastAudience.ACTIVE: "✅ Активным",
    BroadcastAudience.INVITED: "🔑 По ключу",
}
_STATUS_ICONS = {"draft": "📝", "confirmed": "✅", "running": "🔄", "done": "✔️", "failed": "❌"}


def _broadcast_card_text(bc: Broadcast) -> str:
    icon = _STATUS_ICONS.get(bc.status, "❓")
    lines = [
        f"{icon} <b>{bc.title}</b>",
        "",
        f"Аудитория: {_AUDIENCE_LABELS.get(bc.audience, bc.audience)}",
        f"Статус: <b>{bc.get_status_display()}</b>",
        "",
        f"Текст сообщения:",
        f"<blockquote>{truncate(bc.text, 300)}</blockquote>",
    ]
    if bc.total_recipients:
        lines += [
            "",
            f"Получателей: <b>{bc.total_recipients}</b>",
            f"Отправлено: <b>{bc.sent_count}</b> ({bc.delivery_rate}%)",
            f"Ошибок: <b>{bc.failed_count}</b>",
        ]
    if bc.started_at:
        lines.append(f"Запущена: {format_dt(bc.started_at)}")
    if bc.finished_at:
        lines.append(f"Завершена: {format_dt(bc.finished_at)}")
    lines.append(f"\nСоздана: {format_dt(bc.created_at)}")
    return "\n".join(lines)


# ── List ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "broadcasts"), IsAdmin())
async def cb_broadcasts_section(callback: CallbackQuery) -> None:
    await callback.answer()
    broadcasts, total = await sync_to_async(BroadcastService.get_list)(page=1)
    await safe_edit_text(
        callback,
        f"📢 <b>Рассылки</b>\n\nВсего: {total}",
        get_broadcasts_list_keyboard(broadcasts, page=1, total=total),
    )


@router.callback_query(AdminBroadcastCallback.filter(F.action == "list"), IsAdmin())
async def cb_broadcasts_list(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    await callback.answer()
    broadcasts, total = await sync_to_async(BroadcastService.get_list)(page=callback_data.page)
    await safe_edit_text(
        callback,
        f"📢 <b>Рассылки</b>\n\nВсего: {total}",
        get_broadcasts_list_keyboard(broadcasts, page=callback_data.page, total=total),
    )


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "view"), IsAdmin())
async def cb_broadcast_view(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    await callback.answer()
    bc = await sync_to_async(Broadcast.objects.get)(pk=callback_data.broadcast_id)
    await safe_edit_text(callback, _broadcast_card_text(bc), get_broadcast_card_keyboard(bc))


# ── Confirm ───────────────────────────────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "confirm"), IsAdmin())
async def cb_broadcast_confirm(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    bc = await sync_to_async(Broadcast.objects.get)(pk=callback_data.broadcast_id)
    bc = await sync_to_async(BroadcastService.confirm)(bc)
    await callback.answer("Рассылка подтверждена ✅", show_alert=True)
    await safe_edit_text(callback, _broadcast_card_text(bc), get_broadcast_card_keyboard(bc))


# ── Launch (two-step: ask → confirm) ──────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "launch_ask"), IsAdmin())
async def cb_broadcast_launch_ask(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    await callback.answer()
    bc = await sync_to_async(Broadcast.objects.get)(pk=callback_data.broadcast_id)
    recipients_qs = await sync_to_async(BroadcastService.get_recipients_queryset)(bc)
    count = await sync_to_async(recipients_qs.count)()
    await safe_edit_text(
        callback,
        f"🚀 <b>Запустить рассылку?</b>\n\n"
        f"<b>{bc.title}</b>\n"
        f"Аудитория: {_AUDIENCE_LABELS.get(bc.audience, bc.audience)}\n"
        f"Получателей: <b>~{count}</b>\n\n"
        "⚠️ После запуска остановить нельзя.",
        get_broadcast_launch_confirm_keyboard(bc.id),
    )


@router.callback_query(AdminBroadcastCallback.filter(F.action == "launch"), IsAdmin())
async def cb_broadcast_launch(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    bc = await sync_to_async(Broadcast.objects.get)(pk=callback_data.broadcast_id)
    try:
        task_id = await sync_to_async(BroadcastService.launch)(bc)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer("Рассылка запущена! 🚀", show_alert=True)
    bc = await sync_to_async(Broadcast.objects.get)(pk=bc.pk)  # refresh
    await safe_edit_text(callback, _broadcast_card_text(bc), get_broadcast_card_keyboard(bc))


# ── Delivery logs ─────────────────────────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "logs"), IsAdmin())
async def cb_broadcast_logs(callback: CallbackQuery, callback_data: AdminBroadcastCallback) -> None:
    await callback.answer()
    bc = await sync_to_async(Broadcast.objects.get)(pk=callback_data.broadcast_id)
    logs, total = await sync_to_async(BroadcastService.get_delivery_logs)(bc, page=callback_data.page)

    if not logs:
        text = f"📋 Логи рассылки «{bc.title}»\n\nЛогов пока нет."
    else:
        status_icons = {"sent": "✅", "failed": "❌", "blocked": "🚫"}
        lines = [f"📋 <b>Логи доставки</b> — «{truncate(bc.title, 20)}»\n"]
        for log in logs:
            icon = status_icons.get(log.status, "❓")
            lines.append(f"{icon} {log.user.display_name} — {format_dt(log.sent_at, '%H:%M')}")
        text = "\n".join(lines)

    await safe_edit_text(
        callback, text,
        get_broadcast_delivery_logs_keyboard(
            page=callback_data.page, total=total, broadcast_id=callback_data.broadcast_id
        ),
    )


# ── Create (FSM) ──────────────────────────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "create"), IsAdmin())
async def cb_broadcast_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminBroadcastCreateState.waiting_for_title)
    await safe_edit_text(
        callback,
        "➕ <b>Создание рассылки</b>\n\n"
        "Шаг 1/3: Введите <b>внутреннее название</b> рассылки (не отправляется пользователям).",
        get_admin_cancel_keyboard("broadcasts"),
    )


@router.message(AdminBroadcastCreateState.waiting_for_title, IsAdmin())
async def process_broadcast_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Название не может быть пустым.", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(title=title)
    await state.set_state(AdminBroadcastCreateState.waiting_for_text)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await message.answer(
        "Шаг 2/3: Введите <b>текст сообщения</b>.\n\n"
        "<i>Поддерживается HTML-форматирование: &lt;b&gt;, &lt;i&gt;, &lt;code&gt;, &lt;a href&gt;</i>",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AdminBroadcastCreateState.waiting_for_text, IsAdmin())
async def process_broadcast_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        from apps.telegram_bot.keyboards import get_cancel_keyboard
        await message.answer("⚠️ Текст не может быть пустым.", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(text=text)
    await state.set_state(AdminBroadcastCreateState.selecting_audience)
    await message.answer(
        "Шаг 3/3: Выберите <b>аудиторию</b>:",
        reply_markup=get_audience_select_keyboard(),
    )


@router.callback_query(
    AdminBroadcastCallback.filter(F.action.startswith("aud_")),
    AdminBroadcastCreateState.selecting_audience,
    IsAdmin(),
)
async def process_broadcast_audience(
    callback: CallbackQuery, callback_data: AdminBroadcastCallback,
    db_user: User, state: FSMContext,
) -> None:
    # action = "aud_all" | "aud_active" | "aud_invited"
    audience = callback_data.action.removeprefix("aud_")
    data = await state.get_data()
    await state.clear()

    bc = await sync_to_async(BroadcastService.create)(
        title=data["title"],
        text=data["text"],
        audience=audience,
        created_by=db_user,
    )

    await callback.answer("Рассылка создана ✅", show_alert=True)
    await safe_edit_text(callback, _broadcast_card_text(bc), get_broadcast_card_keyboard(bc))


# ── Noop ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminBroadcastCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
