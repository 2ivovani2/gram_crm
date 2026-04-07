"""
Curator: invite key management.
Curators can list, view, and toggle their OWN invite keys, and create new ones.
Uses AdminInviteCallback but with IsCurator() filter, so admin handlers (IsAdmin()) never fire.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async

from apps.invites.models import InviteKey
from apps.invites.services import InviteService
from apps.telegram_bot.admin_keyboards import (
    get_curator_invites_list_keyboard, get_invite_key_card_keyboard,
    get_invite_activations_keyboard,
)
from apps.telegram_bot.callbacks import CuratorCallback, AdminInviteCallback
from apps.telegram_bot.keyboards import get_curator_cancel_keyboard
from apps.telegram_bot.permissions import IsCurator
from apps.telegram_bot.services import safe_edit_text
from apps.telegram_bot.states import AdminInviteCreateState
from apps.users.models import User

router = Router(name="curator_invites")


@router.callback_query(CuratorCallback.filter(F.action == "invites"), IsCurator())
async def cb_curator_invites(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    keys, total = await sync_to_async(InviteService.get_keys_list)(page=1, created_by=db_user)
    text = f"🔑 <b>Ваши инвайт-коды</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_curator_invites_list_keyboard(keys, page=1, total=total))


# Pagination for curator's own keys
@router.callback_query(AdminInviteCallback.filter(F.action == "list"), IsCurator())
async def cb_curator_invites_page(callback: CallbackQuery, callback_data: AdminInviteCallback, db_user: User) -> None:
    await callback.answer()
    keys, total = await sync_to_async(InviteService.get_keys_list)(
        page=callback_data.page, created_by=db_user
    )
    text = f"🔑 <b>Ваши инвайт-коды</b>\n\nВсего: {total}"
    await safe_edit_text(callback, text, get_curator_invites_list_keyboard(keys, page=callback_data.page, total=total))


# ── View & toggle own key ─────────────────────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "view"), IsCurator())
async def cb_curator_key_view(callback: CallbackQuery, callback_data: AdminInviteCallback, db_user: User) -> None:
    await callback.answer()
    try:
        key = await sync_to_async(
            lambda: InviteKey.objects.select_related("created_by").get(pk=callback_data.key_id)
        )()
    except InviteKey.DoesNotExist:
        await callback.answer("Ключ не найден.", show_alert=True)
        return

    # Curators can only see their own keys
    if key.created_by_id != db_user.pk:
        await callback.answer("Нет доступа к этому ключу.", show_alert=True)
        return

    max_uses_str = str(key.max_uses) if key.max_uses else "∞"
    expires_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "бессрочно"
    status = "✅ Активен" if key.is_active else "❌ Деактивирован"
    text = (
        f"🔑 <b>Ключ:</b> <code>{key.key}</code>\n"
        f"📝 Метка: {key.label or '—'}\n"
        f"🔢 Использований: {key.uses_count}/{max_uses_str}\n"
        f"📅 Действует до: {expires_str}\n"
        f"Статус: {status}"
    )
    await safe_edit_text(callback, text, get_invite_key_card_keyboard(key, back_page=callback_data.page))


@router.callback_query(AdminInviteCallback.filter(F.action == "toggle"), IsCurator())
async def cb_curator_key_toggle(callback: CallbackQuery, callback_data: AdminInviteCallback, db_user: User) -> None:
    try:
        key = await sync_to_async(
            lambda: InviteKey.objects.select_related("created_by").get(pk=callback_data.key_id)
        )()
    except InviteKey.DoesNotExist:
        await callback.answer("Ключ не найден.", show_alert=True)
        return

    if key.created_by_id != db_user.pk:
        await callback.answer("Нет доступа к этому ключу.", show_alert=True)
        return

    key = await sync_to_async(InviteService.toggle_active)(key)
    status = "активирован ✅" if key.is_active else "деактивирован ❌"
    await callback.answer(f"Ключ {status}", show_alert=False)

    max_uses_str = str(key.max_uses) if key.max_uses else "∞"
    expires_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "бессрочно"
    text = (
        f"🔑 <b>Ключ:</b> <code>{key.key}</code>\n"
        f"📝 Метка: {key.label or '—'}\n"
        f"🔢 Использований: {key.uses_count}/{max_uses_str}\n"
        f"📅 Действует до: {expires_str}\n"
        f"Статус: {'✅ Активен' if key.is_active else '❌ Деактивирован'}"
    )
    await safe_edit_text(callback, text, get_invite_key_card_keyboard(key, back_page=callback_data.page))


@router.callback_query(AdminInviteCallback.filter(F.action == "activations"), IsCurator())
async def cb_curator_key_activations(callback: CallbackQuery, callback_data: AdminInviteCallback, db_user: User) -> None:
    await callback.answer()
    try:
        key = await sync_to_async(
            lambda: InviteKey.objects.select_related("created_by").get(pk=callback_data.key_id)
        )()
    except InviteKey.DoesNotExist:
        await callback.answer("Ключ не найден.", show_alert=True)
        return

    if key.created_by_id != db_user.pk:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    page = callback_data.page or 1
    activations, total = await sync_to_async(InviteService.get_activations)(key, page=page)

    if not activations:
        await safe_edit_text(
            callback,
            f"📋 Ключ <code>{key.key}</code>\n\nАктиваций пока нет.",
            get_invite_key_card_keyboard(key),
        )
        return

    lines = [f"📋 <b>Активации ключа</b> <code>{key.key[:10]}…</code>\n"]
    for act in activations:
        u = act.user
        lines.append(f"• {u.display_name} ({act.activated_at.strftime('%d.%m.%Y')})")
    await safe_edit_text(
        callback,
        "\n".join(lines),
        get_invite_activations_keyboard(key.id, page, total),
    )


@router.callback_query(AdminInviteCallback.filter(F.action == "noop"), IsCurator())
async def cb_curator_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ── Create key FSM (same as admin flow) ───────────────────────────────────────

@router.callback_query(AdminInviteCallback.filter(F.action == "create"), IsCurator())
async def cb_curator_create_key(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(AdminInviteCreateState.waiting_for_label)
    await safe_edit_text(
        callback,
        "🔑 <b>Создание инвайт-ключа</b>\n\n"
        "Введите метку (описание) для ключа или «-» чтобы пропустить:",
        get_curator_cancel_keyboard(),
    )


@router.message(AdminInviteCreateState.waiting_for_label, IsCurator())
async def curator_process_label(message: Message, state: FSMContext) -> None:
    label = (message.text or "").strip()
    label = "" if label == "-" else label
    await state.update_data(label=label)
    await state.set_state(AdminInviteCreateState.waiting_for_max_uses)
    await message.answer(
        "Максимальное количество использований (целое число) или «-» для неограниченного:",
        reply_markup=get_curator_cancel_keyboard(),
    )


@router.message(AdminInviteCreateState.waiting_for_max_uses, IsCurator())
async def curator_process_max_uses(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        max_uses = None
    else:
        try:
            max_uses = int(raw)
            if max_uses <= 0:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите целое число > 0 или «-».", reply_markup=get_curator_cancel_keyboard())
            return
    await state.update_data(max_uses=max_uses)
    await state.set_state(AdminInviteCreateState.waiting_for_expiry)
    await message.answer(
        "Срок действия (ДД.ММ.ГГГГ) или «-» для бессрочного:",
        reply_markup=get_curator_cancel_keyboard(),
    )


@router.message(AdminInviteCreateState.waiting_for_expiry, IsCurator())
async def curator_process_expiry(message: Message, db_user: User, state: FSMContext) -> None:
    import datetime
    raw = (message.text or "").strip()
    expires_at = None
    if raw != "-":
        try:
            expires_at = datetime.datetime.strptime(raw, "%d.%m.%Y")
        except ValueError:
            await message.answer("⚠️ Введите дату в формате ДД.ММ.ГГГГ или «-».", reply_markup=get_curator_cancel_keyboard())
            return

    data = await state.get_data()
    await state.clear()

    key = await sync_to_async(InviteService.create_key)(
        created_by=db_user,
        label=data.get("label", ""),
        max_uses=data.get("max_uses"),
        expires_at=expires_at,
    )

    from apps.telegram_bot.admin_keyboards import get_invite_key_card_keyboard
    max_uses_str = str(key.max_uses) if key.max_uses else "∞"
    expires_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "бессрочно"
    await message.answer(
        f"✅ <b>Ключ создан!</b>\n\n"
        f"🔑 <code>{key.key}</code>\n"
        f"📝 Метка: {key.label or '—'}\n"
        f"🔢 Использований: 0/{max_uses_str}\n"
        f"📅 Действует до: {expires_str}",
        reply_markup=get_invite_key_card_keyboard(key),
    )
