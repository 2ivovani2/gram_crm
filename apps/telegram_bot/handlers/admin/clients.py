"""
Admin: client and link management via Telegram bot.

Flow:
  Главное меню → Клиенты и ссылки → список клиентов
  → клиент → список его ссылок
  → ссылка → карточка (кто назначен, статистика)
           → деактивировать ссылку (с уведомлением воркера)
           → назначить воркера вручную

Client/link creation is done through the web UI at /stats/clients/.
The bot provides read access + deactivate + manual assignment.
"""
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async

from apps.telegram_bot.callbacks import AdminClientCallback, AdminMenuCallback
from apps.telegram_bot.permissions import IsAdmin
from apps.telegram_bot.services import safe_edit_text
from apps.users.models import User

router = Router(name="admin_clients")

PAGE_SIZE = 8


# ── List clients ──────────────────────────────────────────────────────────────

@router.callback_query(AdminMenuCallback.filter(F.section == "clients"), IsAdmin())
async def cb_clients_menu(callback: CallbackQuery, db_user: User) -> None:
    await callback.answer()
    await _show_clients_list(callback, page=1)


@router.callback_query(AdminClientCallback.filter(F.action == "list"), IsAdmin())
async def cb_clients_list(callback: CallbackQuery, callback_data: AdminClientCallback) -> None:
    await callback.answer()
    await _show_clients_list(callback, page=callback_data.page)


async def _show_clients_list(event, page: int = 1) -> None:
    from apps.clients.models import Client
    from apps.telegram_bot.admin_keyboards import get_clients_list_keyboard

    all_clients = await sync_to_async(lambda: list(Client.objects.prefetch_related("links").order_by("-created_at")))()
    total = len(all_clients)
    start = (page - 1) * PAGE_SIZE
    page_clients = all_clients[start:start + PAGE_SIZE]

    if total == 0:
        text = (
            "🔗 <b>Клиенты и ссылки</b>\n\n"
            "Клиентов пока нет.\n"
            "Добавьте через веб-интерфейс: <b>/stats/clients/</b>"
        )
    else:
        text = f"🔗 <b>Клиенты и ссылки</b> — {total} клиентов"

    markup = get_clients_list_keyboard(page_clients, page, total)
    await safe_edit_text(event, text, markup)


# ── Client card ───────────────────────────────────────────────────────────────

@router.callback_query(AdminClientCallback.filter(F.action == "view_client"), IsAdmin())
async def cb_view_client(callback: CallbackQuery, callback_data: AdminClientCallback) -> None:
    await callback.answer()
    from apps.clients.models import Client
    from apps.telegram_bot.admin_keyboards import get_client_card_keyboard

    client = await sync_to_async(
        lambda: Client.objects.prefetch_related("links__assignments__worker").get(pk=callback_data.client_id)
    )()

    total_apps = await sync_to_async(lambda: client.total_applications)()
    client_earned = await sync_to_async(lambda: client.client_earned)()
    active_links = await sync_to_async(lambda: list(client.active_links))()

    text = (
        f"👤 <b>Клиент: {client.nick}</b>\n"
        f"💰 Ставка: {client.rate} $ / заявка\n"
        f"📊 Всего заявок: <b>{total_apps}</b>\n"
        f"💵 Заработано: <b>{client_earned} $</b>\n"
        f"🔗 Активных ссылок: {len(active_links)}\n\n"
        "Выберите ссылку:"
    )
    await safe_edit_text(callback, text, get_client_card_keyboard(client))


# ── Link card ─────────────────────────────────────────────────────────────────

@router.callback_query(AdminClientCallback.filter(F.action == "view_link"), IsAdmin())
async def cb_view_link(callback: CallbackQuery, callback_data: AdminClientCallback) -> None:
    await callback.answer()
    from apps.clients.models import ClientLink
    from apps.telegram_bot.admin_keyboards import get_link_card_keyboard

    link = await sync_to_async(
        lambda: ClientLink.objects.select_related("client").prefetch_related(
            "assignments__worker", "assignments__work_link"
        ).get(pk=callback_data.link_id)
    )()

    assignment = await sync_to_async(lambda: link.active_assignment)()
    total_apps = await sync_to_async(lambda: link.total_applications)()

    text = (
        f"🔗 <b>Ссылка клиента {link.client.nick}</b>\n\n"
        f"URL: <code>{link.url}</code>\n"
        f"Статус: <b>{'🟢 Активна' if link.status == 'active' else '🔴 Деактивирована'}</b>\n"
        f"📊 Всего заявок: <b>{total_apps}</b>\n"
    )

    if assignment:
        apps = await sync_to_async(lambda: assignment.applications)()
        text += (
            f"\n👷 Исполнитель: <b>{assignment.worker.display_name}</b>\n"
            f"   Заявок (текущее назначение): {apps}\n"
        )
        if assignment.last_count_updated_at:
            text += f"   Последняя активность: {assignment.last_count_updated_at.strftime('%d.%m.%Y %H:%M')}\n"
    else:
        text += "\n👷 Исполнитель: <b>не назначен</b>\n"

    await safe_edit_text(callback, text, get_link_card_keyboard(link))


# ── Deactivate link ───────────────────────────────────────────────────────────

@router.callback_query(AdminClientCallback.filter(F.action == "deactivate"), IsAdmin())
async def cb_deactivate_link(callback: CallbackQuery, callback_data: AdminClientCallback, db_user: User) -> None:
    await callback.answer()
    from apps.clients.models import ClientLink
    from apps.clients.services import AssignmentService

    link = await sync_to_async(
        lambda: ClientLink.objects.select_related("client").get(pk=callback_data.link_id)
    )()

    if link.status != "active":
        await callback.answer("Ссылка уже деактивирована.", show_alert=True)
        return

    # Deactivate + get list of unassigned worker telegram_ids
    unassigned_tg_ids = await sync_to_async(AssignmentService.deactivate_link)(link, note=f"Деактивировано администратором {db_user.display_name}")

    # Notify unassigned workers
    for tg_id in unassigned_tg_ids:
        try:
            await callback.bot.send_message(
                tg_id,
                f"🔴 <b>Ваша ссылка деактивирована</b>\n\n"
                f"Клиент: <b>{link.client.nick}</b>\n"
                f"URL: <code>{link.url}</code>\n\n"
                "На эту ссылку больше не нужно лить заявки.\n"
                "Скоро вам будет выдана новая ссылка.",
            )
        except Exception:
            pass

    await safe_edit_text(
        callback,
        f"✅ Ссылка деактивирована.\nУведомлено воркеров: {len(unassigned_tg_ids)}",
    )


# ── Manual assignment ─────────────────────────────────────────────────────────

@router.callback_query(AdminClientCallback.filter(F.action == "assign_ask"), IsAdmin())
async def cb_assign_ask(callback: CallbackQuery, callback_data: AdminClientCallback) -> None:
    await callback.answer()
    from apps.users.models import User as UserModel, UserRole, UserStatus
    from apps.telegram_bot.admin_keyboards import get_assign_workers_keyboard
    from django.db.models import Count, Q

    workers = await sync_to_async(lambda: list(
        UserModel.objects.filter(
            role__in=[UserRole.WORKER, UserRole.CURATOR],
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
            is_activated=True,
        )
        .annotate(active_assignments=Count("link_assignments", filter=Q(link_assignments__is_active=True)))
        .order_by("active_assignments", "created_at")[:20]
    ))()

    if not workers:
        await callback.answer("Нет доступных воркеров.", show_alert=True)
        return

    await safe_edit_text(
        callback,
        "👷 Выберите воркера для назначения:",
        get_assign_workers_keyboard(workers, callback_data.link_id, callback_data.client_id),
    )


@router.callback_query(AdminClientCallback.filter(F.action == "assign"), IsAdmin())
async def cb_assign(callback: CallbackQuery, callback_data: AdminClientCallback, db_user: User) -> None:
    await callback.answer()
    from apps.clients.models import ClientLink
    from apps.clients.services import AssignmentService
    from apps.users.models import User as UserModel

    link = await sync_to_async(lambda: ClientLink.objects.select_related("client").get(pk=callback_data.link_id))()
    worker = await sync_to_async(lambda: UserModel.objects.get(pk=callback_data.worker_id))()

    assignment = await sync_to_async(AssignmentService.manual_assign)(link, worker)

    # Notify worker
    try:
        await callback.bot.send_message(
            worker.telegram_id,
            f"🔗 <b>Вам назначена ссылка!</b>\n\n"
            f"Клиент: <b>{link.client.nick}</b>\n"
            f"Ставка: {link.client.rate} $ / заявка\n"
            f"URL: <code>{link.url}</code>\n\n"
            "Лейте заявки на эту ссылку. Удачи!",
        )
    except Exception:
        pass

    await safe_edit_text(
        callback,
        f"✅ Воркер <b>{worker.display_name}</b> назначен на ссылку клиента {link.client.nick}.",
    )


# ── Noop ──────────────────────────────────────────────────────────────────────

@router.callback_query(AdminClientCallback.filter(F.action == "noop"), IsAdmin())
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
