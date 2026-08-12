from decimal import Decimal

import pytest

from apps.control.models import Penalty, PenaltyStatus, PenaltyType
from apps.control.services import ControlBalanceService
from apps.control.tasks import _get_available_balance, accrue_daily_rate_task
from apps.users.models import User, UserRole, UserStatus
from apps.withdrawals.models import (
    WithdrawalMethod,
    WithdrawalRequest,
    WithdrawalStatus,
)


pytestmark = pytest.mark.django_db


def _user(telegram_id: int, role=UserRole.WORKER, **kwargs) -> User:
    return User.objects.create(
        telegram_id=telegram_id,
        username=f"user-{telegram_id}",
        role=role,
        status=UserStatus.ACTIVE,
        **kwargs,
    )


def test_set_available_balance_accounts_for_previous_withdrawals_and_penalties():
    worker = _user(2001, daily_accrued=Decimal("1000"))
    WithdrawalRequest.objects.create(
        user=worker,
        amount=Decimal("800"),
        method=WithdrawalMethod.USDT_TRC20,
        details="test-wallet",
        status=WithdrawalStatus.APPROVED,
    )
    Penalty.objects.create(
        user=worker,
        type=PenaltyType.MANUAL,
        amount=Decimal("100"),
        reason="test",
        status=PenaltyStatus.ACCEPTED,
    )

    assert ControlBalanceService.get_available_balance(worker) == Decimal("100")

    ControlBalanceService.set_available_balance(worker, Decimal("650"))
    worker.refresh_from_db()

    assert worker.daily_accrued == Decimal("1000")
    assert worker.manual_balance_adjustment == Decimal("550")
    assert ControlBalanceService.get_available_balance(worker) == Decimal("650")


def test_balance_snapshot_distinguishes_accrual_history_from_available_balance():
    worker = _user(
        2004,
        daily_accrued=Decimal("57000"),
        manual_balance_adjustment=Decimal("4600"),
    )
    WithdrawalRequest.objects.create(
        user=worker,
        amount=Decimal("21600"),
        method=WithdrawalMethod.USDT_TRC20,
        details="test-wallet",
        status=WithdrawalStatus.APPROVED,
    )

    snapshot = ControlBalanceService.get_balance_snapshot(worker)

    assert snapshot["daily"] == Decimal("57000")
    assert snapshot["gross"] == Decimal("61600")
    assert snapshot["withdrawn"] == Decimal("21600")
    assert snapshot["available"] == Decimal("40000")


def test_balance_can_be_set_again_after_another_withdrawal():
    worker = _user(2002, daily_accrued=Decimal("1000"))
    ControlBalanceService.set_available_balance(worker, Decimal("1000"))
    WithdrawalRequest.objects.create(
        user=worker,
        amount=Decimal("1000"),
        method=WithdrawalMethod.USDT_TRC20,
        details="test-wallet",
        status=WithdrawalStatus.APPROVED,
    )
    assert ControlBalanceService.get_available_balance(worker) == Decimal("0")

    ControlBalanceService.set_available_balance(worker, Decimal("400"))
    worker.refresh_from_db()

    assert ControlBalanceService.get_available_balance(worker) == Decimal("400")


def test_deadline_balance_uses_control_ledger_and_reserves_pending_withdrawal():
    worker = _user(2003, daily_accrued=Decimal("1000"))
    ControlBalanceService.set_available_balance(worker, Decimal("1000"))
    WithdrawalRequest.objects.create(
        user=worker,
        amount=Decimal("300"),
        method=WithdrawalMethod.USDT_TRC20,
        details="test-wallet",
        status=WithdrawalStatus.PENDING,
    )

    gross, available = _get_available_balance(worker)

    assert gross == Decimal("1000")
    assert available == Decimal("700")


@pytest.mark.parametrize(
    "role",
    [UserRole.WORKER, UserRole.CURATOR, UserRole.ACCOUNTANT, UserRole.ADMIN],
)
def test_daily_rate_accrues_for_every_active_employee_role(monkeypatch, role):
    employee = _user(2100 + len(role), role=role, daily_rate=Decimal("125"))
    sent = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent.append((telegram_id, text)) or True,
    )

    result = accrue_daily_rate_task(force=True)
    employee.refresh_from_db()

    assert result["accrued"] == 1
    assert result["notification_sent"] == 1
    assert employee.daily_accrued == Decimal("125")
    assert employee.daily_rate_last_accrued_date is not None
    assert sent[0][0] == employee.telegram_id
    assert "+125.00 ₽" in sent[0][1]
    assert "Доступный баланс" in sent[0][1]


def test_daily_rate_is_idempotent_for_same_date(monkeypatch):
    employee = _user(2201, daily_rate=Decimal("300"))
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda *args, **kwargs: True,
    )

    first = accrue_daily_rate_task(force=True)
    second = accrue_daily_rate_task(force=True)
    employee.refresh_from_db()

    assert first["accrued"] == 1
    assert second["accrued"] == 0
    assert employee.daily_accrued == Decimal("300")


def test_daily_rate_skips_inactive_and_anonymous_users(monkeypatch):
    inactive = _user(2301, daily_rate=Decimal("100"))
    inactive.status = UserStatus.INACTIVE
    inactive.save(update_fields=["status"])
    anonymous = _user(
        2302,
        role=UserRole.ANONYMOUS,
        daily_rate=Decimal("100"),
    )
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda *args, **kwargs: True,
    )

    result = accrue_daily_rate_task(force=True)
    inactive.refresh_from_db()
    anonymous.refresh_from_db()

    assert result["accrued"] == 0
    assert inactive.daily_accrued == Decimal("0")
    assert anonymous.daily_accrued == Decimal("0")
