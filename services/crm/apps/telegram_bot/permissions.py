"""
aiogram Filters for role/status permission checks.

Usage in handlers:
    @router.message(Command("admin"), IsAdmin())
    @router.callback_query(SomeCallback.filter(), IsActivatedWorker())

Filters receive `db_user` from UserMiddleware via handler data injection.
If db_user is None (e.g. anonymous channel post), all filters return False.
"""
from __future__ import annotations
from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
from asgiref.sync import sync_to_async
from apps.users.models import User, UserRole, UserStatus


MANAGEMENT_PERMISSIONS = (
    "reports.view", "templates.view", "penalties.view", "penalties.create",
    "employees.view", "withdrawals.view", "notifications.manage", "access.manage",
)


async def get_bot_access(user):
    """Never retain this snapshot in FSM: every update must read current DB grants."""
    from apps.crm.access import get_access
    return await sync_to_async(get_access)(user)


async def bot_can(user, code: str) -> bool:
    from apps.crm.access import has_permission
    return await sync_to_async(has_permission)(user, code)


async def permitted_recipient_ids(telegram_ids, code: str) -> list[int]:
    """Notification recipients need current authorization too, not only callback guards."""
    from apps.crm.access import has_permission

    def resolve():
        return [user.telegram_id for user in User.objects.filter(
            telegram_id__in=telegram_ids, is_blocked_bot=False,
        ).order_by("pk") if has_permission(user, code)]

    return await sync_to_async(resolve)()


class CanOpenManagement(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user=None) -> bool:
        access = await get_bot_access(db_user)
        return any(access.allows(code) for code in MANAGEMENT_PERMISSIONS)


def callback_permissions(raw: str) -> tuple[str, ...] | None:
    """Any-of requirements for registered operational callbacks, not all Telegram traffic.

    Unknown actions under these five protected prefixes fail closed. Other prefixes
    (including invitation enrollment) are deliberately outside this adapter.
    """
    parts = raw.split(":")
    prefix = parts[0]
    action = parts[1] if len(parts) > 1 else ""
    if prefix == "ctrl_w":
        return ("personal.use",)
    if prefix == "adm":
        return {"main": (*MANAGEMENT_PERMISSIONS, "personal.use"),
                "users": ("employees.view",)}.get(action, ())
    if prefix == "adm_u":
        if action in {"list", "view", "search", "noop"}:
            return ("employees.view",)
        if action in {"change_status", "set_active", "set_inactive", "set_banned"}:
            return ("access.manage",)
        if action in {"set_curator", "set_worker"}:
            return ("access.manage",)
        return ()
    if prefix == "ctrl_acc":
        if action in {"withdrawals", "w_view"}:
            return ("withdrawals.view",)
        if action in {"w_processing", "w_receipt", "w_done", "w_reject"}:
            return ("withdrawals.manage",)
        return ()
    if prefix == "ctrl_a":
        if action == "main":
            return (*MANAGEMENT_PERMISSIONS, "personal.use")
        if action in {"reports", "reports_mine", "reports_all", "rep_view"}:
            return ("reports.view",)
        if action in {"rep_accept", "rep_reject"}:
            return ("reports.moderate",)
        if action in {"penalties", "pen_view"}:
            return ("penalties.view",)
        if action == "create_penalty":
            return ("penalties.create",)
        if action in {"pen_accept", "pen_reject", "pen_delete"}:
            return ("penalties.resolve",)
        if action in {"broadcast", "bc_confirm", "bc_cancel"}:
            return ("notifications.manage",)
        if action in {"withdraw", "addr_sel", "addr_new", "my_addresses", "add_addr", "del_addr"}:
            return ("personal.use",)
        return ()
    return None


def state_permissions(raw_state: str | None) -> tuple[str, ...] | None:
    group = (raw_state or "").split(":", 1)[0]
    if group in {"SubmitReportState", "WithdrawalState", "CryptoAddressState",
                 "DisputePenaltyState", "AdminWithdrawalState", "AdminCryptoAddressState"}:
        return ("personal.use",)
    return {
        "AdminReportReviewState": ("reports.moderate",),
        "AdminPenaltyCreateState": ("penalties.create",),
        "AdminBroadcastControlState": ("notifications.manage",),
        "AdminUserSearchState": ("employees.view",),
    }.get(group)


class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        return db_user is not None and db_user.role == UserRole.ADMIN


class IsCurator(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        return (
            db_user is not None
            and db_user.role == UserRole.CURATOR
            and db_user.status not in (UserStatus.INACTIVE, UserStatus.BANNED)
        )


class IsActivatedWorker(BaseFilter):
    """
    Passes for:
      - activated workers (is_activated=True, status not inactive/banned)
      - curators (always treated as activated, same status guard)
    Used on shared flows (withdrawal, back_to_start, etc.)
    """
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        if db_user is None:
            return False
        if db_user.status in (UserStatus.INACTIVE, UserStatus.BANNED):
            return False
        if db_user.role == UserRole.CURATOR:
            return True
        return db_user.is_activated


class IsNotActivated(BaseFilter):
    """Matches users who have NOT yet been activated (pending join request or none)."""
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        if db_user is None:
            return True
        return not db_user.is_activated


class IsNotBanned(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery, db_user: User | None = None) -> bool:
        if db_user is None:
            return True
        return db_user.status != UserStatus.BANNED
