import datetime as dt
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.control.models import (
    ControlSettings,
    EmployeeReport,
    Penalty,
    PenaltyStatus,
    PenaltyType,
    ReportStatus,
    ReportTemplate,
)
from apps.control.services import EmployeeService
from apps.control.tasks import _process_report_deadlines, notify_penalty_created_task
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


def _user(telegram_id: int, username: str, role=UserRole.WORKER) -> User:
    return User.objects.create(
        telegram_id=telegram_id,
        telegram_username=username,
        username=f"test-{telegram_id}",
        role=role,
        status=UserStatus.ACTIVE,
    )


def test_new_template_penalty_works_when_global_penalty_is_zero(monkeypatch):
    worker = _user(71001, "new_template_worker")
    submitted_template = ReportTemplate.objects.create(
        name="Submitted",
        auto_penalty_amount=Decimal("500"),
    )
    missing_template = ReportTemplate.objects.create(
        name="New required template",
        auto_penalty_amount=Decimal("750"),
    )
    submitted_template.assigned_users.add(worker)
    missing_template.assigned_users.add(worker)

    from zoneinfo import ZoneInfo
    now = dt.datetime(2026, 8, 16, 12, 5, tzinfo=ZoneInfo("Europe/Moscow"))
    report_date = now.date()
    deadline = now - dt.timedelta(minutes=5)
    EmployeeReport.objects.create(
        user=worker,
        template=submitted_template,
        report_date=report_date,
        status=ReportStatus.ACCEPTED,
        first_submission_at=deadline - dt.timedelta(hours=1),
    )
    settings = ControlSettings.get()
    settings.late_report_penalty_amount = Decimal("0")
    settings.save(update_fields=["late_report_penalty_amount"])
    sent = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent.append((telegram_id, text)) or True,
    )

    submitted_template.deadline_time = deadline.time().replace(tzinfo=None)
    submitted_template.save(update_fields=["deadline_time"])
    missing_template.deadline_time = submitted_template.deadline_time
    missing_template.save(update_fields=["deadline_time"])
    result = _process_report_deadlines(now=now)

    penalty = Penalty.objects.get(user=worker, template=missing_template)
    assert result["initial"] == 1
    assert penalty.template == missing_template
    assert penalty.report_date == report_date
    assert penalty.amount == Decimal("750")
    assert "New required template" in penalty.reason
    second = _process_report_deadlines(now=now)
    assert second["initial"] == 0
    assert Penalty.objects.filter(user=worker).count() == 1


def test_manual_penalty_notification_escapes_reason(monkeypatch):
    worker = _user(71002, "notify_worker")
    penalty = Penalty.objects.create(
        user=worker,
        type=PenaltyType.MANUAL,
        amount=Decimal("1200"),
        reason="Ошибка <script>",
        status=PenaltyStatus.CREATED,
    )
    sent = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent.append((telegram_id, text, reply_markup)) or True,
    )

    result = notify_penalty_created_task(penalty.pk)

    assert result["sent"] is True
    assert sent[0][0] == worker.telegram_id
    assert "Ошибка &lt;script&gt;" in sent[0][1]
    assert sent[0][2] is not None


def test_archiving_employee_preserves_reports_and_penalties(monkeypatch):
    worker = _user(71003, "archived_worker")
    report = EmployeeReport.objects.create(
        user=worker,
        report_date=timezone.localdate(),
        status=ReportStatus.ACCEPTED,
    )
    penalty = Penalty.objects.create(
        user=worker,
        type=PenaltyType.MANUAL,
        amount=Decimal("100"),
        reason="Historical record",
        status=PenaltyStatus.ACCEPTED,
    )
    revoked = []
    monkeypatch.setattr(
        "apps.crm.identity.terminate_user_sessions",
        lambda user_id: revoked.append(user_id) or 0,
    )

    EmployeeService.archive(worker)
    worker.refresh_from_db()

    assert worker.status == UserStatus.INACTIVE
    assert EmployeeReport.objects.filter(pk=report.pk, user=worker).exists()
    assert Penalty.objects.filter(pk=penalty.pk, user=worker).exists()
    assert revoked == [worker.pk]
