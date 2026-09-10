import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User as TelegramUser
from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied

from apps.crm.access import Access, ROLE_PERMISSIONS
from apps.telegram_bot import permissions
from apps.telegram_bot.admin_keyboards import get_admin_main_menu, get_user_card_keyboard
from apps.telegram_bot.middleware import AccessMiddleware
from apps.control.bot.keyboards import admin_report_actions, admin_penalty_actions, accountant_withdrawal_actions
from apps.users.models import UserRole


def message(text=None):
    return Message(message_id=1, date=dt.datetime.now(dt.timezone.utc),
                   chat=Chat(id=42, type="private"), from_user=TelegramUser(id=42, is_bot=False, first_name="Test"),
                   text=text)


def callback(raw):
    return CallbackQuery(id="test", from_user=TelegramUser(id=42, is_bot=False, first_name="Test"),
                         chat_instance="test", data=raw, message=message())


def state(name=None):
    return SimpleNamespace(get_state=AsyncMock(return_value=name), clear=AsyncMock())


def callbacks(markup):
    return {button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data}


@pytest.mark.parametrize("terminal_error", [False, True])
async def test_withdrawal_handler_leaves_employee_notice_to_service(monkeypatch, terminal_error):
    from apps.control.bot import accountant_handlers
    from apps.control.services import ControlWithdrawalService
    from apps.withdrawals.models import WithdrawalRequest

    actor = SimpleNamespace(pk=42, telegram_id=42, telegram_username="test_accountant")
    withdrawal = SimpleNamespace(pk=7, user_id=43, admin_notifications=[])
    query = Mock()
    query.filter.return_value.first.return_value = withdrawal
    monkeypatch.setattr(WithdrawalRequest.objects, "select_related", Mock(return_value=query))
    monkeypatch.setattr(accountant_handlers, "bot_can", AsyncMock(return_value=True))
    monkeypatch.setattr(accountant_handlers, "get_bot_access", AsyncMock(
        return_value=Access(role=UserRole.ACCOUNTANT, allowed=frozenset({"withdrawals.manage"})),
    ))
    operation = Mock(side_effect=ValueError("Заявка уже завершена") if terminal_error else None)
    monkeypatch.setattr(ControlWithdrawalService, "mark_completed", operation)
    event = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock()),
                            bot=SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock()))

    await accountant_handlers._update_withdrawal(event, SimpleNamespace(obj_id=7), actor, "done")

    operation.assert_called_once_with(withdrawal, actor)
    event.bot.send_message.assert_not_awaited()
    if terminal_error:
        event.answer.assert_awaited_once_with("Заявка уже завершена", show_alert=True)
        event.message.edit_text.assert_not_awaited()
    else:
        event.message.edit_text.assert_awaited_once()


@pytest.mark.parametrize(("raw", "required"), [
    ("ctrl_a:reports:0", "reports.view"), ("ctrl_a:rep_view:4", "reports.view"),
    ("ctrl_a:rep_accept:4", "reports.moderate"), ("ctrl_a:rep_reject:4", "reports.moderate"),
    ("ctrl_a:pen_view:4", "penalties.view"), ("ctrl_a:create_penalty:0", "penalties.create"),
    ("ctrl_a:pen_accept:4", "penalties.resolve"), ("ctrl_a:pen_reject:4", "penalties.resolve"),
    ("ctrl_a:pen_delete:4", "penalties.resolve"), ("ctrl_a:bc_confirm:0", "notifications.manage"),
    ("ctrl_acc:withdrawals:0", "withdrawals.view"), ("ctrl_acc:w_view:4", "withdrawals.view"),
    ("ctrl_acc:w_done:4", "withdrawals.manage"), ("ctrl_acc:w_reject:4", "withdrawals.manage"),
    ("adm:users", "employees.view"), ("adm_u:search:0:1", "employees.view"),
    ("adm_u:set_active:4:1", "access.manage"), ("adm_u:set_curator:4:1", "access.manage"),
    ("ctrl_w:submit_report", "personal.use"), ("ctrl_w:dl.all.0", "personal.use"),
    ("ctrl_a:withdraw:0", "personal.use"), ("ctrl_a:del_addr:4", "personal.use"),
])
def test_callback_permission_mapping(raw, required):
    assert permissions.callback_permissions(raw) == (required,)


@pytest.mark.parametrize(("name", "required"), [
    ("SubmitReportState:waiting_for_report", "personal.use"),
    ("WithdrawalState:waiting_for_wallet", "personal.use"),
    ("CryptoAddressState:waiting_for_address", "personal.use"),
    ("DisputePenaltyState:waiting_for_comment", "personal.use"),
    ("AdminWithdrawalState:waiting_for_amount", "personal.use"),
    ("AdminCryptoAddressState:waiting_for_name", "personal.use"),
    ("AdminPenaltyCreateState:waiting_for_username", "penalties.create"),
    ("AdminPenaltyCreateState:waiting_for_amount", "penalties.create"),
    ("AdminPenaltyCreateState:waiting_for_reason", "penalties.create"),
    ("AdminReportReviewState:waiting_for_comment", "reports.moderate"),
    ("AdminBroadcastControlState:waiting_for_text", "notifications.manage"),
    ("AdminUserSearchState:waiting_for_query", "employees.view"),
])
def test_each_current_fsm_step_rechecks_permission(name, required):
    assert permissions.state_permissions(name) == (required,)


async def test_stale_callback_rechecks_fresh_permissions_each_time(monkeypatch):
    get_access = AsyncMock(side_effect=[Access(allowed=frozenset({"reports.moderate"})), Access()])
    monkeypatch.setattr(permissions, "get_bot_access", get_access)
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    handler = AsyncMock()
    data = {"db_user": SimpleNamespace(pk=42), "state": state()}
    middleware = AccessMiddleware()
    await middleware(handler, callback("ctrl_a:rep_accept:4"), data)
    await middleware(handler, callback("ctrl_a:rep_accept:4"), data)
    assert get_access.await_count == 2
    handler.assert_awaited_once()
    data["state"].clear.assert_awaited_once()
    assert answer.call_args.kwargs["show_alert"] is True


@pytest.mark.parametrize("text", [None, "", "   "])
async def test_media_or_empty_fsm_message_cannot_bypass_revocation(monkeypatch, text):
    monkeypatch.setattr(permissions, "get_bot_access", AsyncMock(return_value=Access()))
    monkeypatch.setattr(Message, "answer", AsyncMock())
    handler = AsyncMock()
    fsm = state("AdminPenaltyCreateState:waiting_for_reason")
    await AccessMiddleware()(handler, message(text), {"db_user": SimpleNamespace(pk=42), "state": fsm})
    handler.assert_not_awaited()
    fsm.clear.assert_awaited_once()


@pytest.mark.parametrize("event", [message("/start"), message("/start@GramlyCRMBot"), callback("wi:accept:7"), message()])
async def test_nonoperational_start_and_invitation_are_not_blanket_blocked(monkeypatch, event):
    resolver = AsyncMock(side_effect=AssertionError("not an operational permission gate"))
    monkeypatch.setattr(permissions, "get_bot_access", resolver)
    handler = AsyncMock()
    await AccessMiddleware()(handler, event, {"db_user": None, "state": state()})
    handler.assert_awaited_once()
    resolver.assert_not_awaited()


async def test_unknown_protected_action_denies_but_unrelated_prefix_is_untouched(monkeypatch):
    monkeypatch.setattr(permissions, "get_bot_access", AsyncMock(return_value=Access(allowed=frozenset({"access.manage"})) ))
    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    handler = AsyncMock()
    await AccessMiddleware()(handler, callback("ctrl_a:future_unsafe_action:4"), {"db_user": None})
    handler.assert_not_awaited()
    await AccessMiddleware()(handler, callback("third_party:action"), {"db_user": None})
    handler.assert_awaited_once()


async def test_transaction_time_denial_is_actionable_and_clears_fsm(monkeypatch):
    monkeypatch.setattr(permissions, "get_bot_access", AsyncMock(return_value=Access(allowed=frozenset({"penalties.create"}))))
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    fsm = state("AdminPenaltyCreateState:waiting_for_reason")
    await AccessMiddleware()(AsyncMock(side_effect=PermissionDenied()), message("reason"), {"state": fsm})
    fsm.clear.assert_awaited_once()
    assert "Доступ" in answer.call_args.args[0]


def test_curator_menu_exposes_moderation_and_proposals_not_resolution_or_admin():
    access = Access(role=UserRole.CURATOR, allowed=ROLE_PERMISSIONS[UserRole.CURATOR])
    actions = callbacks(get_admin_main_menu(access))
    assert {"ctrl_a:reports:0", "ctrl_a:penalties:0", "ctrl_a:create_penalty:0", "ctrl_w:main_menu"} <= actions
    assert not {"adm:users", "ctrl_a:broadcast:0", "ctrl_acc:withdrawals:0"} & actions
    assert callbacks(admin_penalty_actions(4, can_resolve=access.allows("penalties.resolve"))) == {"ctrl_a:penalties:0"}


async def test_worker_default_keeps_personal_start_despite_web_calculator(monkeypatch):
    access = Access(role=UserRole.WORKER, allowed=ROLE_PERMISSIONS[UserRole.WORKER])
    monkeypatch.setattr(permissions, "get_bot_access", AsyncMock(return_value=access))
    assert access.allows("calculator.use")
    assert not await permissions.CanOpenManagement()(message("/start"), SimpleNamespace(pk=42))


@pytest.mark.parametrize("text", [None, "", "Infinity", "NaN", "0", "-500"])
async def test_penalty_amount_invalid_input_keeps_form_and_does_not_mutate(monkeypatch, text):
    from apps.control.bot.admin_handlers import penalty_got_amount
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    fsm = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    await penalty_got_amount(message(text), fsm)
    fsm.update_data.assert_not_awaited()
    fsm.set_state.assert_not_awaited()
    assert "корректную сумму" in answer.call_args.args[0]


def test_view_only_cards_do_not_offer_mutation_buttons():
    assert callbacks(admin_report_actions(4)) == {"ctrl_a:reports:0"}
    assert callbacks(accountant_withdrawal_actions(4)) == {"ctrl_acc:withdrawals:0"}
    assert "ctrl_a:rep_accept:4" in callbacks(admin_report_actions(4, can_moderate=True))
    assert "ctrl_acc:w_done:4" in callbacks(accountant_withdrawal_actions(4, can_manage=True))
    assert callbacks(get_user_card_keyboard(SimpleNamespace(id=4))) == {"adm_u:list:0:1"}


def test_menu_grants_and_revocations_not_just_role_labels():
    access = Access(role=UserRole.WORKER, allowed=frozenset({"employees.view", "withdrawals.view"}))
    actions = callbacks(get_admin_main_menu(access))
    assert actions == {"adm:users", "ctrl_acc:withdrawals:0"}
    assert callbacks(get_admin_main_menu(Access(role=UserRole.ADMIN))) == set()


async def test_old_role_callback_never_changes_role_and_opens_access_form(monkeypatch):
    from apps.telegram_bot.handlers.admin.users import cb_set_role
    from apps.telegram_bot.callbacks import AdminUserCallback
    from apps.users.services import UserService
    monkeypatch.setattr(UserService, "set_role", lambda *args, **kwargs: pytest.fail("legacy role mutation"))
    event = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
    await cb_set_role(event, AdminUserCallback(action="set_curator", user_id=4))
    text = event.message.answer.call_args.args[0]
    assert "подтвердить" in text
    assert event.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].url.endswith("/crm/control/users/")


def test_employee_card_without_finance_permission_does_not_read_balance():
    from apps.telegram_bot.handlers.admin.users import _user_card_text
    user = SimpleNamespace(status="active", role="worker", telegram_id=42, display_name="Test",
        telegram_username=None, is_activated=True, is_blocked_bot=False,
        created_at=None, last_activity_at=None, get_role_display=lambda: "worker", get_status_display=lambda: "active")
    # No balance/rate fields deliberately: a non-financial view must not access them.
    text = _user_card_text(user)
    assert "Карточка пользователя" in text
    assert "Баланс" not in text and "ставка" not in text


@pytest.mark.django_db
def test_bot_adapter_ignores_stale_user_and_sees_revoked_membership():
    from apps.crm.models import Workspace, WorkspaceMembership
    from apps.users.models import User
    ws = Workspace.objects.create(name="Gramly", slug="gramly")
    user = User.objects.create(telegram_id=800001, role="curator", status="active")
    member = WorkspaceMembership.objects.create(workspace=ws, user=user, role="viewer")
    assert async_to_sync(permissions.bot_can)(user, "reports.moderate")
    member.permission_overrides = {"reports.moderate": False}
    member.save(update_fields=["permission_overrides"])
    assert not async_to_sync(permissions.bot_can)(user, "reports.moderate")
    member.is_active = False
    member.save(update_fields=["is_active"])
    assert not async_to_sync(permissions.bot_can)(user, "personal.use")


@pytest.mark.django_db
def test_invitation_acceptance_provisions_without_sso_and_preserves_assigned_role():
    from apps.control.bot.invite_handlers import respond_to_invite
    from apps.control.models import WorkerInvite
    from apps.crm.models import Workspace, WorkspaceMembership
    from apps.users.models import User
    ws = Workspace.objects.create(name="Gramly", slug="gramly")
    user = User.objects.create(telegram_id=800002, role="curator", status="pending")
    invite = WorkerInvite.objects.create(user=user)
    respond_to_invite(invite.pk, user, accept=True)
    user.refresh_from_db()
    assert user.role == "curator" and user.status == "active"
    assert not user.oidc_subject
    assert WorkspaceMembership.objects.get(user=user, workspace=ws).is_active
    with pytest.raises(ValueError, match="уже ответили"):
        respond_to_invite(invite.pk, user, accept=True)
    assert WorkspaceMembership.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_invitation_does_not_restore_revoked_workspace_access():
    from apps.control.bot.invite_handlers import respond_to_invite
    from apps.control.models import WorkerInvite
    from apps.crm.models import Workspace, WorkspaceMembership
    from apps.users.models import User
    ws = Workspace.objects.create(name="Gramly", slug="gramly")
    user = User.objects.create(telegram_id=800003, role="worker", status="active")
    member = WorkspaceMembership.objects.create(workspace=ws, user=user, role="viewer", is_active=False)
    invite = WorkerInvite.objects.create(user=user)
    respond_to_invite(invite.pk, user, accept=True)
    member.refresh_from_db()
    assert not member.is_active


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["banned", "inactive"])
def test_old_invitation_cannot_reactivate_disabled_user(status):
    from apps.control.bot.invite_handlers import respond_to_invite
    from apps.control.models import WorkerInvite
    from apps.users.models import User
    user = User.objects.create(telegram_id=800004, role="anonymous", status="pending")
    invite = WorkerInvite.objects.create(user=user)
    User.objects.filter(pk=user.pk).update(status=status)
    with pytest.raises(PermissionDenied):
        respond_to_invite(invite.pk, user, accept=True)
    invite.refresh_from_db()
    assert invite.status == "pending"


@pytest.mark.django_db
def test_invitation_is_bound_to_its_original_employee():
    from apps.control.bot.invite_handlers import respond_to_invite
    from apps.control.models import WorkerInvite
    from apps.users.models import User
    owner = User.objects.create(telegram_id=800005, status="active")
    other = User.objects.create(telegram_id=800006, status="active")
    invite = WorkerInvite.objects.create(user=owner)
    with pytest.raises(ValueError, match="не найдено"):
        respond_to_invite(invite.pk, other, accept=True)
