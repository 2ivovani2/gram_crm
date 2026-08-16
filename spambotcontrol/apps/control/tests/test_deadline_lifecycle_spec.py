import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction

from apps.control.models import (
    EmployeeReport,
    Penalty,
    PenaltySource,
    ReportStatus,
    ReportTemplate,
)
from apps.control.services import ReportService
from apps.control.tasks import _process_report_deadlines, notify_report_decision_task
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db
MSK = ZoneInfo("Europe/Moscow")


def _user(telegram_id=81001):
    return User.objects.create(
        telegram_id=telegram_id,
        username=f"deadline-{telegram_id}",
        telegram_username=f"deadline{telegram_id}",
        role=UserRole.WORKER,
        status=UserStatus.ACTIVE,
    )


def _admin(telegram_id=81999):
    return User.objects.create(
        telegram_id=telegram_id,
        username=f"admin-{telegram_id}",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )


def _template(worker, deadline=dt.time(23, 0), amount="500"):
    template = ReportTemplate.objects.create(
        name="Daily sales",
        deadline_time=deadline,
        auto_penalty_amount=Decimal(amount),
    )
    template.assigned_users.add(worker)
    return template


def _set_now(monkeypatch, value):
    monkeypatch.setattr("apps.control.services.timezone.now", lambda: value)


def test_first_rejection_after_deadline_starts_one_hour_and_repeated_rejection_does_not_extend(monkeypatch):
    worker = _user()
    admin = _admin()
    template = _template(worker)
    submitted = dt.datetime(2026, 8, 16, 22, 0, tzinfo=MSK)
    _set_now(monkeypatch, submitted)
    report = ReportService.submit_report(worker, template, text="v1")

    first_rejection = dt.datetime(2026, 8, 17, 0, 30, tzinfo=MSK)
    _set_now(monkeypatch, first_rejection)
    ReportService.reject_report(report, admin, "fix")
    report.refresh_from_db()
    original_end = first_rejection + dt.timedelta(hours=1)
    assert report.correction_started_at == first_rejection
    assert report.correction_deadline == original_end

    resubmitted = dt.datetime(2026, 8, 17, 1, 0, tzinfo=MSK)
    _set_now(monkeypatch, resubmitted)
    ReportService.submit_report(worker, template, text="v2", report_id=report.pk)
    second_rejection = dt.datetime(2026, 8, 17, 1, 10, tzinfo=MSK)
    _set_now(monkeypatch, second_rejection)
    ReportService.reject_report(report, admin, "still wrong")
    report.refresh_from_db()
    assert report.correction_deadline == original_end
    assert report.can_user_edit(second_rejection)


def test_version_sent_before_correction_end_can_be_accepted_later_without_penalty(monkeypatch):
    worker = _user(81002)
    admin = _admin(81998)
    template = _template(worker)
    _set_now(monkeypatch, dt.datetime(2026, 8, 16, 22, 0, tzinfo=MSK))
    report = ReportService.submit_report(worker, template, text="v1")
    _set_now(monkeypatch, dt.datetime(2026, 8, 17, 0, 0, tzinfo=MSK))
    ReportService.reject_report(report, admin, "fix")
    _set_now(monkeypatch, dt.datetime(2026, 8, 17, 0, 55, tzinfo=MSK))
    ReportService.submit_report(worker, template, text="v2", report_id=report.pk)

    _process_report_deadlines(now=dt.datetime(2026, 8, 17, 1, 5, tzinfo=MSK))
    assert not Penalty.objects.filter(
        report=report,
        source=PenaltySource.CORRECTION_EXPIRED,
    ).exists()

    _set_now(monkeypatch, dt.datetime(2026, 8, 17, 1, 30, tzinfo=MSK))
    ReportService.accept_report(report, admin, "ok")
    report.refresh_from_db()
    assert report.status == ReportStatus.ACCEPTED
    assert not Penalty.objects.filter(report=report).exists()


def test_accepted_report_is_final_and_cannot_be_decided_twice():
    worker = _user(81012)
    admin = _admin(81988)
    template = _template(worker)
    report = ReportService.submit_report(worker, template, text="ready")

    ReportService.accept_report(report, admin, "ok")
    report.refresh_from_db()

    assert not ReportService.can_moderate(admin, report)
    with pytest.raises(ValueError, match="уже принято решение"):
        ReportService.reject_report(report, admin, "stale action")


def test_missing_primary_report_gets_first_and_additional_penalties_once():
    worker = _user(81003)
    template = _template(worker)
    deadline = dt.datetime(2026, 8, 16, 23, 0, tzinfo=MSK)

    first = _process_report_deadlines(now=deadline)
    assert first["initial"] == 1
    assert Penalty.objects.filter(
        user=worker,
        template=template,
        report_date=deadline.date(),
        source=PenaltySource.DEADLINE_MISSED,
    ).count() == 1

    expired = _process_report_deadlines(now=deadline + dt.timedelta(hours=24))
    assert expired["additional"] == 1
    assert Penalty.objects.filter(
        user=worker,
        template=template,
        report_date=deadline.date(),
        source=PenaltySource.LATE_WINDOW_EXPIRED,
    ).count() == 1

    repeated = _process_report_deadlines(now=deadline + dt.timedelta(hours=24, minutes=1))
    assert repeated["additional"] == 0


def test_late_report_uses_fixed_window_and_rejection_after_window_creates_second_penalty(monkeypatch):
    worker = _user(81004)
    admin = _admin(81997)
    template = _template(worker)
    deadline = dt.datetime(2026, 8, 16, 23, 0, tzinfo=MSK)
    _process_report_deadlines(now=deadline)

    submitted = deadline + dt.timedelta(hours=5)
    _set_now(monkeypatch, submitted)
    report = ReportService.submit_report(worker, template, text="late")
    assert report.is_late_submission
    assert report.late_window_ends_at == deadline + dt.timedelta(hours=24)

    rejected = deadline + dt.timedelta(hours=25)
    _set_now(monkeypatch, rejected)
    ReportService.reject_report(report, admin, "too late")
    assert Penalty.objects.filter(
        report=report,
        source=PenaltySource.LATE_WINDOW_EXPIRED,
    ).exists()


def test_database_rejects_duplicate_user_template_date():
    worker = _user(81005)
    template = _template(worker)
    report_date = dt.date(2026, 8, 16)
    EmployeeReport.objects.create(user=worker, template=template, report_date=report_date)
    with pytest.raises(IntegrityError), transaction.atomic():
        EmployeeReport.objects.create(user=worker, template=template, report_date=report_date)


def test_decision_notification_contains_required_fields(monkeypatch):
    worker = _user(81006)
    admin = _admin(81996)
    template = _template(worker)
    submitted = dt.datetime(2026, 8, 16, 22, 0, tzinfo=MSK)
    _set_now(monkeypatch, submitted)
    report = ReportService.submit_report(worker, template, text="v1")
    decided = dt.datetime(2026, 8, 17, 0, 15, tzinfo=MSK)
    _set_now(monkeypatch, decided)
    ReportService.reject_report(report, admin, "Нужно исправить")
    sent = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent.append((telegram_id, text, reply_markup)) or True,
    )

    result = notify_report_decision_task(report.pk)

    assert result["sent"] is True
    assert f"ID отчёта: {report.pk}" in sent[0][1]
    assert "Дата отчёта: 16.08.2026" in sent[0][1]
    assert "Время решения: 00:15" in sent[0][1]
    assert "Нужно исправить" in sent[0][1]
    assert "Редактирование: Доступно до 01:15" in sent[0][1]
    assert sent[0][2] is not None
