"""
Admin: invite key management.
  - list with pagination
  - key card view
  - toggle active/inactive
  - activation log
  - multi-step key creation (FSM)
"""
from __future__ import annotations
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.invites.models import InviteKey
from apps.invites.services import InviteService
from apps.telegram_bot.admin_keyboards import (
    get_invites_list_keyboard, get_invite_key_card_keyboard,
    get_invite_activations_keyboard, get_admin_cancel_keyboard,
    get_admin_main_menu,
)
from apps.telegram_bot.callbacks import AdminMenuCallback, AdminInviteCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text, answer_and_edit
from apps.telegram_bot.states import AdminInviteCreateState
from apps.users.models import User
from apps.common.utils import format_dt

router = Router(name="admin_invites")


def _key_card_text(key: InviteKey) -> str:
    lines = [
        "🔑 <b>Invite Key</b>",
        "",
        f"Ключ: <code>{key.key}</code>",
        f"Метка: {key.label or '—'}",
        f"Статус: <b>{key.get_status_label()}</b>",
        "",
        f"Использований: <b>{key.uses_count}</b> / {key.max_uses or '∞'}",
        f"Осталось: {key.remaining_uses if key.max_uses else '∞'}",
        f"Истекает: {format_dt(key.expires_at) if key.expires_at else '—'}",
        "",
        f"Создан: {format_dt(key.created_at)}",
    ]
    return "\n".join(lines)


# ── List ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "invites"), IsAdmin())
async def cb_invites_section(callback: CallbackQuery) -> None:
    await callback.answer()
    keys, total = await sync_to_async(InviteService.get_keys_list)(page=1)
    await safe_edit_text(
        callback,
        f"🔑 <b>Invite Keys</b>\n\nВсего: {total}",
        get_invites_list_keyboard(keys, page=1, total=total),
    )


@router.callback_query(AdminInviteCallback.filter(F.action == "list"), IsAdmin())
async def cb_invites_list(callback: CallbackQuery, callback_data: AdminInviteCallback) -> None:
    await callback.answer()
    keys, total = await sync_to_async(InviteService.get_keys_list)(page=callback_data.page)
    await safe_edit_text(
        callback,
        f"🔑 <b>Invite Keys</b>\n\nВсего: {total}",
        get_invites_list_keyboard(keys, page=callback_data.page, total=total),
    )


# ── View ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "view"), IsAdmin())
async def cb_invite_view(callback: CallbackQuery, callback_data: AdminInviteCallback) -> None:
    await callback.answer()
    key = await sync_to_async(InviteKey.objects.get)(pk=callback_data.key_id)
    await safe_edit_text(callback, _key_card_text(key), get_invite_key_card_keyboard(key, back_page=callback_data.page))


# ── Toggle ────────────────────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "toggle"), IsAdmin())
async def cb_invite_toggle(callback: CallbackQuery, callback_data: AdminInviteCallback) -> None:
    key = await sync_to_async(InviteKey.objects.get)(pk=callback_data.key_id)
    key = await sync_to_async(InviteService.toggle_active)(key)
    status_word = "активирован" if key.is_active else "деактивирован"
    await callback.answer(f"Ключ {status_word}", show_alert=True)
    await safe_edit_text(callback, _key_card_text(key), get_invite_key_card_keyboard(key, back_page=callback_data.page))


# ── Activations log ───────────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "activations"), IsAdmin())
async def cb_invite_activations(callback: CallbackQuery, callback_data: AdminInviteCallback) -> None:
    await callback.answer()
    key = await sync_to_async(InviteKey.objects.get)(pk=callback_data.key_id)
    activations, total = await sync_to_async(InviteService.get_activations)(key, page=callback_data.page)

    if not activations:
        text = f"📋 Активации ключа <code>{key.key}</code>\n\nНет активаций."
    else:
        lines = [f"📋 <b>Активации</b> ключа <code>{key.key[:8]}…</code>\n"]
        for act in activations:
            lines.append(f"• {act.user.display_name} — {format_dt(act.activated_at)}")
        text = "\n".join(lines)

    await safe_edit_text(
        callback, text,
        get_invite_activations_keyboard(key_id=callback_data.key_id, page=callback_data.page, total=total),
    )


# ── Create (FSM) ──────────────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "create"), IsAdmin())
async def cb_invite_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminInviteCreateState.waiting_for_label)
    await safe_edit_text(
        callback,
        "➕ <b>Создание invite key</b>\n\n"
        "Шаг 1/3: Введите <b>метку</b> ключа (внутреннее название).\n"
        "Отправьте «-» если метка не нужна.",
        get_admin_cancel_keyboard("invites"),
    )


@router.message(AdminInviteCreateState.waiting_for_label, IsAdmin())
async def process_invite_label(message: Message, state: FSMContext) -> None:
    label = (message.text or "").strip()
    label = "" if label == "-" else label
    await state.update_data(label=label)
    await state.set_state(AdminInviteCreateState.waiting_for_max_uses)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await message.answer(
        "Шаг 2/3: Введите <b>лимит использований</b>.\n"
        "Отправьте «0» или «-» для неограниченного.",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AdminInviteCreateState.waiting_for_max_uses, IsAdmin())
async def process_invite_max_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    max_uses = None
    if raw not in ("-", "0"):
        try:
            max_uses = int(raw)
            if max_uses <= 0:
                max_uses = None
        except ValueError:
            from apps.telegram_bot.keyboards import get_cancel_keyboard
            await message.answer("⚠️ Введите целое число или «-».", reply_markup=get_cancel_keyboard())
            return

    await state.update_data(max_uses=max_uses)
    await state.set_state(AdminInviteCreateState.waiting_for_expiry)
    from apps.telegram_bot.keyboards import get_cancel_keyboard
    await message.answer(
        "Шаг 3/3: Введите <b>срок действия</b> в формате ДД.ММ.ГГГГ.\n"
        "Отправьте «-» для бессрочного ключа.",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AdminInviteCreateState.waiting_for_expiry, IsAdmin())
async def process_invite_expiry(message: Message, db_user: User, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    expires_at = None

    if raw != "-":
        from datetime import datetime
        from django.utils import timezone as dj_timezone
        try:
            dt = datetime.strptime(raw, "%d.%m.%Y")
            expires_at = dj_timezone.make_aware(dt)
        except ValueError:
            from apps.telegram_bot.keyboards import get_cancel_keyboard
            await message.answer("⚠️ Неверный формат. Используйте ДД.ММ.ГГГГ.", reply_markup=get_cancel_keyboard())
            return

    data = await state.get_data()
    await state.clear()

    key = await sync_to_async(InviteService.create_key)(
        created_by=db_user,
        label=data.get("label", ""),
        max_uses=data.get("max_uses"),
        expires_at=expires_at,
    )

    await message.answer(
        f"✅ <b>Ключ создан!</b>\n\n"
        f"Ключ: <code>{key.key}</code>\n"
        f"Лимит: {key.max_uses or '∞'}\n"
        f"Истекает: {format_dt(key.expires_at) if key.expires_at else '—'}",
        reply_markup=get_admin_main_menu(),
    )


# ── Noop ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
