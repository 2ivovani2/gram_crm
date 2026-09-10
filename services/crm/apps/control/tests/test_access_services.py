"""Service boundaries re-check CRM access, even for stale web/FSM callers."""
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.control.models import EmployeeReport, Penalty, PenaltyStatus, PenaltyType, ReportStatus
from apps.control.services import ControlBalanceService, PenaltyService, ReportService
from apps.control.services import ControlWithdrawalService, FinancialConditionService, EmployeeService
from apps.control.tasks import notify_penalty_created_task
from apps.crm.models import Workspace, WorkspaceMembership
from apps.users.models import User, UserRole, UserStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def user_factory():
    workspace = Workspace.objects.create(slug="gramly", name="Gramly")

    def create(role=UserRole.WORKER, overrides=None):
        user = User.objects.create(telegram_id=90000 + User.objects.count(), role=role,
                                   status=UserStatus.ACTIVE, daily_accrued=Decimal("1000"))
        WorkspaceMembership.objects.create(user=user, workspace=workspace,
                                           permission_overrides=overrides or {})
        return user
    return create


@pytest.fixture(autouse=True)
def no_sends(monkeypatch):
    enqueue = Mock(return_value=True)
    monkeypatch.setattr("apps.control.tasks.queue_penalty_notification", enqueue)
    monkeypatch.setattr("apps.control.tasks.queue_report_decision_notification", Mock())
    monkeypatch.setattr("apps.control.tasks._send_message_sync", Mock(return_value=True))
    monkeypatch.setattr("apps.control.tasks.queue_financial_condition_notification", Mock())
    monkeypatch.setattr("apps.control.services.ControlWithdrawalService._notify_sync", Mock())
    return enqueue


def report_for(user):
    return EmployeeReport.objects.create(user=user, status=ReportStatus.ON_MODERATION)


@pytest.mark.parametrize("action", ["accept_report", "reject_report", "send_to_revision"])
def test_self_moderation_denied_even_admin(user_factory, action):
    admin = user_factory(UserRole.ADMIN)
    report = report_for(admin)
    with pytest.raises(PermissionDenied):
        getattr(ReportService, action)(report, admin)
    report.refresh_from_db()
    assert report.status == ReportStatus.ON_MODERATION
    assert not report.history.exists()


@pytest.mark.parametrize("target_role,allowed", [(UserRole.WORKER, True), (UserRole.ACCOUNTANT, True),
                                                (UserRole.CURATOR, False), (UserRole.ADMIN, False)])
def test_curator_target_boundaries(user_factory, target_role, allowed):
    curator = user_factory(UserRole.CURATOR)
    report = report_for(user_factory(target_role))
    assert ReportService.can_moderate(curator, report) is allowed
    if allowed:
        ReportService.accept_report(report, curator)
    else:
        with pytest.raises(PermissionDenied):
            ReportService.accept_report(report, curator)


def test_stale_role_and_individual_revocation_block_service(user_factory):
    curator = user_factory(UserRole.CURATOR)
    worker = user_factory()
    report = report_for(worker)
    assert ReportService.can_moderate(curator, report)
    WorkspaceMembership.objects.filter(user=curator).update(permission_overrides={"reports.moderate": False})
    with pytest.raises(PermissionDenied):
        ReportService.reject_report(report, curator)
    WorkspaceMembership.objects.filter(user=curator).update(permission_overrides={})
    User.objects.filter(pk=worker.pk).update(role=UserRole.ADMIN)
    assert not ReportService.can_moderate(curator, report)


def test_explicit_moderation_grant_and_inactive_denial(user_factory):
    moderator = user_factory(UserRole.ACCOUNTANT, {"reports.moderate": True})
    report = report_for(user_factory())
    assert ReportService.can_moderate(moderator, report)
    User.objects.filter(pk=moderator.pk).update(status=UserStatus.BANNED)
    with pytest.raises(PermissionDenied):
        ReportService.accept_report(report, moderator)


def test_proposal_does_not_change_balance_and_is_pending(user_factory, no_sends, django_capture_on_commit_callbacks):
    curator, worker = user_factory(UserRole.CURATOR), user_factory()
    before = ControlBalanceService.get_available_balance(worker)
    with django_capture_on_commit_callbacks(execute=True):
        penalty = PenaltyService.create_manual(curator, worker, Decimal("250"), "Reason")
        no_sends.assert_not_called()
        assert penalty.status == PenaltyStatus.CREATED
        assert penalty in PenaltyService.get_pending_penalties()
        assert ControlBalanceService.get_available_balance(worker) == before
    no_sends.assert_called_once_with(penalty.pk, expected_status=PenaltyStatus.CREATED)


@pytest.mark.parametrize("action", ["accept", "reject", "delete"])
def test_curator_cannot_resolve_proposals(user_factory, action):
    curator, worker = user_factory(UserRole.CURATOR), user_factory()
    penalty = PenaltyService.create_manual(curator, worker, Decimal("100"), "Reason")
    with pytest.raises(PermissionDenied):
        PenaltyService.resolve(penalty, curator, action)
    penalty.refresh_from_db()
    assert penalty.status == PenaltyStatus.CREATED


def test_resolve_once_and_recheck_permission_before_idempotent_return(user_factory, no_sends,
                                                                     django_capture_on_commit_callbacks):
    admin, worker = user_factory(UserRole.ADMIN), user_factory()
    penalty = PenaltyService.create_manual(admin, worker, Decimal("100"), "Reason")
    with django_capture_on_commit_callbacks(execute=True):
        first = PenaltyService.resolve(penalty, admin, "accept")
        second = PenaltyService.resolve(penalty, admin, "accept")
        no_sends.assert_not_called()
    assert first.resolved_at == second.resolved_at
    no_sends.assert_called_once_with(penalty.pk, expected_status=PenaltyStatus.ACCEPTED)
    assert ControlBalanceService.get_available_balance(worker) == Decimal("900")
    WorkspaceMembership.objects.filter(user=admin).update(is_active=False)
    with pytest.raises(PermissionDenied):
        PenaltyService.resolve(penalty, admin, "accept")


def test_explicit_resolve_grant_and_model_boundary(user_factory):
    curator = user_factory(UserRole.CURATOR, {"penalties.resolve": True})
    penalty = PenaltyService.create_manual(curator, user_factory(), Decimal("100"), "Reason")
    penalty.accept(curator)
    assert penalty.status == PenaltyStatus.ACCEPTED
    WorkspaceMembership.objects.filter(user=curator).update(permission_overrides={})
    with pytest.raises(PermissionDenied):
        penalty.reject(curator)
    with pytest.raises(PermissionDenied):
        penalty.delete_soft(curator)


def test_cancel_proposal_silent_and_resolved_terminal(user_factory, no_sends,
                                                     django_capture_on_commit_callbacks):
    admin, worker = user_factory(UserRole.ADMIN), user_factory()
    penalty = PenaltyService.create_manual(admin, worker, Decimal("100"), "Reason")
    with django_capture_on_commit_callbacks(execute=True):
        PenaltyService.resolve(penalty, admin, "reject")
    no_sends.assert_not_called()
    with pytest.raises(ValueError):
        PenaltyService.resolve(penalty, admin, "accept")
    assert ControlBalanceService.get_available_balance(worker) == Decimal("1000")


def test_rollback_never_schedules_or_persists_proposal(user_factory, no_sends,
                                                    django_capture_on_commit_callbacks):
    admin, worker = user_factory(UserRole.ADMIN), user_factory()
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            PenaltyService.create_manual(admin, worker, Decimal("100"), "Reason")
            raise RuntimeError("rollback")
    assert Penalty.objects.count() == 0
    no_sends.assert_not_called()


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "0.001", "10000000000"])
def test_invalid_proposal_amount_rejected(user_factory, amount):
    with pytest.raises(ValueError):
        PenaltyService.create_manual(user_factory(UserRole.ADMIN), user_factory(), Decimal(amount), "Reason")
    assert not Penalty.objects.exists()


def test_proposal_notifies_admin_not_employee_then_confirmed_charge(user_factory, monkeypatch):
    admin, curator, worker = user_factory(UserRole.ADMIN), user_factory(UserRole.CURATOR), user_factory()
    penalty = PenaltyService.create_manual(curator, worker, Decimal("100"), "Reason <tag>")
    send = Mock(return_value=True)
    monkeypatch.setattr("apps.control.tasks._send_message_sync", send)
    result = notify_penalty_created_task(penalty.pk)
    assert result["proposal"]
    assert send.call_args.args[0] == admin.telegram_id
    assert "Баланс сотрудника не изменён" in send.call_args.args[1]
    assert "&lt;tag&gt;" in send.call_args.args[1]
    send.reset_mock()
    PenaltyService.resolve(penalty, admin, "accept")
    # A delayed proposal task must not become a second employee charge notice.
    stale = notify_penalty_created_task(penalty.pk, expected_status=PenaltyStatus.CREATED)
    assert stale["reason"] == "penalty_state_changed"
    send.assert_not_called()
    notify_penalty_created_task(penalty.pk)
    send.assert_called_once()
    assert send.call_args.args[0] == worker.telegram_id
    assert "Вам начислен штраф" in send.call_args.args[1]


def test_system_auto_penalties_unchanged(user_factory):
    worker = user_factory()
    penalty = PenaltyService.create_auto(worker, amount=Decimal("100"))
    assert penalty.type == PenaltyType.AUTO
    assert penalty.status == PenaltyStatus.ACCEPTED
    assert ControlBalanceService.get_available_balance(worker) == Decimal("900")


def withdrawal_for(user):
    from apps.withdrawals.models import WithdrawalRequest, WithdrawalMethod
    return WithdrawalRequest.objects.create(user=user, amount=Decimal("100"),
                                             method=WithdrawalMethod.USDT_TRC20, details="test-wallet")


@pytest.mark.parametrize("method", ["mark_processing", "mark_receipt_sent", "mark_completed", "reject"])
def test_withdrawal_mutations_deny_curator_and_revoked_accountant(user_factory, method):
    curator, accountant = user_factory(UserRole.CURATOR), user_factory(UserRole.ACCOUNTANT)
    withdrawal = withdrawal_for(user_factory())
    with pytest.raises(PermissionDenied):
        getattr(ControlWithdrawalService, method)(withdrawal, curator)
    WorkspaceMembership.objects.filter(user=accountant).update(is_active=False)
    with pytest.raises(PermissionDenied):
        getattr(ControlWithdrawalService, method)(withdrawal, accountant)
    withdrawal.refresh_from_db()
    assert withdrawal.status == "pending"


def test_self_withdrawal_allowed_only_fresh_accountant(user_factory):
    admin, accountant = user_factory(UserRole.ADMIN), user_factory(UserRole.ACCOUNTANT)
    with pytest.raises(PermissionDenied):
        ControlWithdrawalService.mark_completed(withdrawal_for(admin), admin)
    own = withdrawal_for(accountant)
    ControlWithdrawalService.mark_processing(own, accountant)
    User.objects.filter(pk=accountant.pk).update(role=UserRole.ADMIN)
    with pytest.raises(PermissionDenied):
        ControlWithdrawalService.mark_completed(own, accountant)


def test_withdrawal_terminal_idempotency_and_post_commit_notice(user_factory, monkeypatch,
                                                              django_capture_on_commit_callbacks):
    accountant, worker = user_factory(UserRole.ACCOUNTANT), user_factory()
    withdrawal = withdrawal_for(worker)
    notify = Mock()
    monkeypatch.setattr(ControlWithdrawalService, "_notify_sync", notify)
    with django_capture_on_commit_callbacks(execute=True):
        first = ControlWithdrawalService.mark_completed(withdrawal, accountant)
        second = ControlWithdrawalService.mark_completed(withdrawal, accountant)
        assert first.processed_at == second.processed_at
        notify.assert_not_called()
    notify.assert_called_once()
    for method in ("mark_processing", "mark_receipt_sent", "reject"):
        with pytest.raises(ValueError):
            getattr(ControlWithdrawalService, method)(withdrawal, accountant)
    WorkspaceMembership.objects.filter(user=accountant).update(permission_overrides={"withdrawals.manage": False})
    with pytest.raises(PermissionDenied):
        ControlWithdrawalService.mark_completed(withdrawal, accountant)


def test_withdrawal_rollback_silent_and_cannot_regress_receipt(user_factory, monkeypatch,
                                                           django_capture_on_commit_callbacks):
    accountant = user_factory(UserRole.ACCOUNTANT)
    withdrawal = withdrawal_for(user_factory())
    notify = Mock()
    monkeypatch.setattr(ControlWithdrawalService, "_notify_sync", notify)
    with django_capture_on_commit_callbacks(execute=True):
        with pytest.raises(RuntimeError), transaction.atomic():
            ControlWithdrawalService.mark_completed(withdrawal, accountant)
            raise RuntimeError("rollback")
    notify.assert_not_called()
    withdrawal.refresh_from_db()
    assert withdrawal.status == "pending"
    ControlWithdrawalService.mark_receipt_sent(withdrawal, accountant)
    with pytest.raises(ValueError):
        ControlWithdrawalService.mark_processing(withdrawal, accountant)


@pytest.mark.parametrize("operation", ["create", "save_address", "delete_address"])
def test_personal_withdrawal_operations_check_revocation(user_factory, operation):
    worker = user_factory()
    address = ControlWithdrawalService.save_address(worker, "Test", "test-wallet")
    WorkspaceMembership.objects.filter(user=worker).update(permission_overrides={"personal.use": False})
    calls = {"create": lambda: ControlWithdrawalService.create(worker, "test-wallet"),
             "save_address": lambda: ControlWithdrawalService.save_address(worker, "Other", "other-wallet"),
             "delete_address": lambda: ControlWithdrawalService.delete_address(worker, address.pk)}
    with pytest.raises(PermissionDenied):
        calls[operation]()
    address.refresh_from_db()


def test_financial_update_denies_even_noop_and_accepts_explicit_grant(user_factory):
    from apps.control.models import FinancialConditionChange
    curator, worker = user_factory(UserRole.CURATOR), user_factory()
    with pytest.raises(PermissionDenied):
        FinancialConditionService.update(worker_id=worker.pk, admin=curator, daily_rate=None, available_balance=None)
    assert not FinancialConditionChange.objects.exists()
    WorkspaceMembership.objects.filter(user=curator).update(permission_overrides={"employees.finance": True})
    _, event = FinancialConditionService.update(worker_id=worker.pk, admin=curator,
                                               daily_rate=Decimal("250"), available_balance=None)
    assert event.new_daily_rate == Decimal("250")
    User.objects.filter(pk=curator.pk).update(status=UserStatus.BANNED)
    with pytest.raises(PermissionDenied):
        FinancialConditionService.update(worker_id=worker.pk, admin=curator, daily_rate=None, available_balance=None)


def test_withdrawal_processor_extras_need_authorization(user_factory):
    from apps.control.models import ControlSettings
    accountant, worker = user_factory(UserRole.ACCOUNTANT), user_factory()
    extra = user_factory(UserRole.CURATOR, {"withdrawals.view": True})
    ControlSettings.get().withdrawal_processors.add(worker, extra, accountant)
    assert set(ControlWithdrawalService.get_processor_ids()) == {accountant.telegram_id, extra.telegram_id}
    WorkspaceMembership.objects.filter(user=accountant).update(is_active=False)
    assert ControlWithdrawalService.get_processor_ids() == [extra.telegram_id]


def test_archive_requires_actor_permission_and_is_idempotent(user_factory, monkeypatch,
                                                          django_capture_on_commit_callbacks):
    curator, admin, worker = user_factory(UserRole.CURATOR), user_factory(UserRole.ADMIN), user_factory()
    revoke = Mock()
    monkeypatch.setattr("apps.crm.identity.terminate_user_sessions", revoke)
    with pytest.raises(PermissionDenied):
        EmployeeService.archive(worker, actor=curator)
    with django_capture_on_commit_callbacks(execute=True):
        EmployeeService.archive(worker, actor=admin)
        EmployeeService.archive(worker, actor=admin)
        revoke.assert_not_called()
    revoke.assert_called_once_with(worker.pk)
    worker.refresh_from_db()
    assert worker.status == UserStatus.INACTIVE
