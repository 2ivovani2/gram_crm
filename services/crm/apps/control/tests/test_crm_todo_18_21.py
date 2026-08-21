import datetime as dt
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command

from apps.control.deadlines import DeadlineFilter, DeadlineState, ReportDeadlineProvider, calculate_report_deadline
from apps.control.models import (
    EmployeeReport,
    FinancialConditionChange,
    Penalty,
    PenaltySource,
    PenaltyStatus,
    PenaltyType,
    ReportStatus,
    ReportTemplate,
)
from apps.control.services import (
    ControlBalanceService,
    FinancialConditionService,
    ReportDeadlineService,
    ReportService,
)
from apps.control.tasks import _process_report_deadlines, notify_financial_condition_change_task
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db
MSK = ZoneInfo("Europe/Moscow")


def _user(telegram_id, *, role=UserRole.WORKER, username=None):
    return User.objects.create(
        telegram_id=telegram_id,
        telegram_username=username or f"user{telegram_id}",
        username=f"internal-{telegram_id}",
        first_name="Иван",
        role=role,
        status=UserStatus.ACTIVE,
    )


def _template(worker, *, name="Daily", deadline=dt.time(0, 0), amount="500"):
    template = ReportTemplate.objects.create(
        name=name,
        deadline_time=deadline,
        auto_penalty_amount=Decimal(amount),
    )
    template.assigned_users.add(worker)
    return template


def test_midnight_is_end_of_reporting_day():
    assert calculate_report_deadline(dt.date(2026, 8, 20), dt.time(0, 0)) == dt.datetime(
        2026, 8, 21, 0, 0, tzinfo=MSK,
    )
    assert calculate_report_deadline(dt.date(2026, 8, 20), dt.time(23, 0)) == dt.datetime(
        2026, 8, 20, 23, 0, tzinfo=MSK,
    )


def test_midnight_penalty_is_not_created_before_real_deadline():
    worker = _user(92001)
    template = _template(worker)
    before = dt.datetime(2026, 8, 20, 23, 59, tzinfo=MSK)

    _process_report_deadlines(now=before)

    assert not Penalty.objects.filter(
        user=worker, template=template, report_date=before.date(),
    ).exists()


def test_timely_midnight_report_waiting_for_moderation_has_no_penalty():
    worker = _user(92002)
    template = _template(worker)
    report_date = dt.date(2026, 8, 20)
    report = EmployeeReport.objects.create(
        user=worker,
        template=template,
        report_date=report_date,
        status=ReportStatus.ON_MODERATION,
        first_submission_at=dt.datetime(2026, 8, 20, 23, 59, tzinfo=MSK),
        last_submission_at=dt.datetime(2026, 8, 20, 23, 59, tzinfo=MSK),
        deadline_at=dt.datetime(2026, 8, 20, 0, 0, tzinfo=MSK),  # legacy broken row
    )

    result = _process_report_deadlines(now=dt.datetime(2026, 8, 21, 0, 1, tzinfo=MSK))

    assert result == {"initial": 0, "correction": 0, "additional": 0}
    assert not Penalty.objects.filter(report=report).exists()


def test_each_template_obligation_is_evaluated_independently():
    worker = _user(92003)
    midnight = _template(worker, name="Midnight", deadline=dt.time(0, 0))
    early = _template(worker, name="Early", deadline=dt.time(23, 0))
    now = dt.datetime(2026, 8, 20, 23, 30, tzinfo=MSK)

    _process_report_deadlines(now=now)

    assert Penalty.objects.filter(user=worker, template=early, report_date=now.date()).exists()
    assert not Penalty.objects.filter(user=worker, template=midnight, report_date=now.date()).exists()


def test_deadline_provider_filters_multiple_templates_and_hides_accepted():
    worker = _user(92004)
    first = _template(worker, name="Контент", deadline=dt.time(23, 0))
    second = _template(worker, name="Финансы", deadline=dt.time(0, 0))
    now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=MSK)
    EmployeeReport.objects.create(
        user=worker,
        template=first,
        report_date=now.date(),
        status=ReportStatus.ON_MODERATION,
        first_submission_at=now,
        deadline_at=calculate_report_deadline(now.date(), first.deadline_time),
        editing_locked_at=calculate_report_deadline(now.date(), first.deadline_time),
    )
    EmployeeReport.objects.create(
        user=worker,
        template=second,
        report_date=now.date(),
        status=ReportStatus.ACCEPTED,
        first_submission_at=now,
        deadline_at=calculate_report_deadline(now.date(), second.deadline_time),
    )

    today = ReportDeadlineProvider.list_for_user(worker, DeadlineFilter.TODAY, now=now)
    tomorrow = ReportDeadlineProvider.list_for_user(worker, DeadlineFilter.TOMORROW, now=now)
    week = ReportDeadlineProvider.list_for_user(worker, DeadlineFilter.WEEK, now=now)

    assert any(item.template_id == first.pk and item.state == DeadlineState.MODERATION for item in today)
    assert not any(item.template_id == second.pk and item.report_date == now.date() for item in tomorrow)
    assert any(item.template_id == second.pk and item.report_date == now.date() + dt.timedelta(days=1) for item in week)


def test_old_accepted_report_suppresses_active_penalty_projection():
    worker = _user(92014)
    template = _template(worker)
    now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=MSK)
    report_date = now.date() - dt.timedelta(days=20)
    report = EmployeeReport.objects.create(
        user=worker,
        template=template,
        report_date=report_date,
        status=ReportStatus.ACCEPTED,
        first_submission_at=calculate_report_deadline(report_date, template.deadline_time),
        deadline_at=calculate_report_deadline(report_date, template.deadline_time),
    )
    Penalty.objects.create(
        user=worker,
        template=template,
        report=report,
        report_date=report_date,
        source=PenaltySource.DEADLINE_MISSED,
        type=PenaltyType.AUTO,
        amount=Decimal("500"),
        reason="historical",
        status=PenaltyStatus.ACCEPTED,
    )

    items = ReportDeadlineProvider.list_for_user(worker, DeadlineFilter.ALL, now=now)

    assert not any(item.report_date == report_date for item in items)


def test_explicit_deadline_date_creates_the_selected_obligation(monkeypatch):
    worker = _user(92015)
    template = _template(worker)
    now = dt.datetime(2026, 8, 20, 2, 0, tzinfo=MSK)
    monkeypatch.setattr("apps.control.services.timezone.now", lambda: now)

    report = ReportService.submit_report(
        worker,
        template,
        text="late report",
        explicit_report_date=now.date() - dt.timedelta(days=1),
    )

    assert report.report_date == now.date() - dt.timedelta(days=1)


def test_financial_change_is_atomic_snapshot_and_noop_does_not_create_event(monkeypatch):
    worker = _user(92005)
    admin = _user(92995, role=UserRole.ADMIN)
    queued = []
    monkeypatch.setattr(
        "apps.control.tasks.queue_financial_condition_notification",
        lambda event_id: queued.append(event_id) or True,
    )

    worker, event = FinancialConditionService.update(
        worker_id=worker.pk,
        admin=admin,
        daily_rate=Decimal("600"),
        available_balance=Decimal("12000"),
    )
    assert event.previous_daily_rate == Decimal("0")
    assert event.new_daily_rate == Decimal("600")
    assert event.previous_available_balance == Decimal("0")
    assert event.new_available_balance == Decimal("12000")
    assert ControlBalanceService.get_available_balance(worker) == Decimal("12000")

    _, second = FinancialConditionService.update(
        worker_id=worker.pk,
        admin=admin,
        daily_rate=Decimal("600"),
        available_balance=Decimal("12000"),
    )
    assert second is None
    assert FinancialConditionChange.objects.count() == 1


def test_financial_notification_is_identical_for_both_recipients(monkeypatch):
    worker = _user(92006)
    admin = _user(92996, role=UserRole.ADMIN)
    event = FinancialConditionChange.objects.create(
        worker=worker,
        changed_by=admin,
        previous_daily_rate=Decimal("500"),
        new_daily_rate=Decimal("700"),
        previous_available_balance=Decimal("10000"),
        new_available_balance=Decimal("8000"),
    )
    sent = []
    monkeypatch.setattr(
        "apps.control.tasks._send_message_sync",
        lambda telegram_id, text, reply_markup=None: sent.append((telegram_id, text)) or True,
    )

    result = notify_financial_condition_change_task.run(event.pk)

    assert result["sent"] is True
    assert [row[0] for row in sent] == [worker.telegram_id, admin.telegram_id]
    assert sent[0][1] == sent[1][1]
    assert "Было: <b>500.00 ₽</b>" in sent[0][1]
    assert "Стало: <b>8000.00 ₽</b>" in sent[0][1]


def test_remediation_dry_run_then_apply_restores_balance_and_is_idempotent(monkeypatch):
    worker = _user(92007)
    worker.daily_accrued = Decimal("1000")
    worker.save(update_fields=["daily_accrued", "updated_at"])
    template = _template(worker)
    report_date = dt.date.today()
    report = EmployeeReport.objects.create(
        user=worker,
        template=template,
        report_date=report_date,
        status=ReportStatus.ON_MODERATION,
        first_submission_at=dt.datetime.combine(report_date, dt.time(23, 0), tzinfo=MSK),
        last_submission_at=dt.datetime.combine(report_date, dt.time(23, 0), tzinfo=MSK),
        deadline_at=dt.datetime.combine(report_date, dt.time(0, 0), tzinfo=MSK),
        editing_locked_at=dt.datetime.combine(report_date, dt.time(0, 0), tzinfo=MSK),
    )
    penalty = Penalty.objects.create(
        user=worker,
        template=template,
        report=report,
        report_date=report_date,
        source=PenaltySource.DEADLINE_MISSED,
        type=PenaltyType.AUTO,
        amount=Decimal("500"),
        reason="legacy",
        status=PenaltyStatus.ACCEPTED,
    )
    monkeypatch.setattr("apps.control.tasks.notify_penalty_remediation_task.delay", lambda *args: None)
    before = ControlBalanceService.get_available_balance(worker)
    dry = StringIO()
    call_command("repair_midnight_deadlines", stdout=dry)
    penalty.refresh_from_db()
    assert penalty.status == PenaltyStatus.ACCEPTED

    call_command("repair_midnight_deadlines", apply=True, stdout=StringIO())
    penalty.refresh_from_db()
    report.refresh_from_db()
    assert penalty.status == PenaltyStatus.REJECTED
    assert report.deadline_at.astimezone(MSK).date() == report_date + dt.timedelta(days=1)
    assert ControlBalanceService.get_available_balance(worker) == before + Decimal("500")

    call_command("repair_midnight_deadlines", apply=True, stdout=StringIO())
    assert Penalty.objects.get(pk=penalty.pk).status == PenaltyStatus.REJECTED


def test_remediation_restores_premature_correction_window(monkeypatch):
    worker = _user(92016)
    template = _template(worker)
    report_date = dt.date.today()
    old_deadline = dt.datetime.combine(report_date, dt.time(0, 0), tzinfo=MSK)
    submitted = old_deadline + dt.timedelta(hours=12)
    report = EmployeeReport.objects.create(
        user=worker,
        template=template,
        report_date=report_date,
        status=ReportStatus.OVERDUE,
        first_submission_at=submitted,
        last_submission_at=submitted,
        deadline_at=old_deadline,
        is_late_submission=True,
        late_window_ends_at=old_deadline + dt.timedelta(hours=24),
        correction_started_at=submitted + dt.timedelta(hours=1),
        correction_deadline=submitted + dt.timedelta(hours=2),
        editing_locked_at=submitted + dt.timedelta(hours=2),
    )
    monkeypatch.setattr("apps.control.tasks.notify_penalty_remediation_task.delay", lambda *args: None)

    call_command("repair_midnight_deadlines", apply=True, stdout=StringIO())

    report.refresh_from_db()
    assert report.deadline_at == old_deadline + dt.timedelta(days=1)
    assert report.status == ReportStatus.REJECTED
    assert report.is_late_submission is False
    assert report.late_window_ends_at is None
    assert report.correction_started_at is None
    assert report.correction_deadline is None
    assert report.editing_locked_at == report.deadline_at


def test_premature_remediated_penalty_can_be_reactivated_when_deadline_arrives(monkeypatch):
    worker = _user(92017)
    template = _template(worker)
    report_date = dt.date(2026, 8, 20)
    penalty = Penalty.objects.create(
        user=worker,
        template=template,
        report_date=report_date,
        source=PenaltySource.DEADLINE_MISSED,
        type=PenaltyType.AUTO,
        amount=Decimal("500"),
        reason="legacy premature",
        status=PenaltyStatus.REJECTED,
        comment="[AUTO-REMEDIATION:PREMATURE] repaired",
    )
    monkeypatch.setattr("apps.control.tasks.queue_auto_penalty_notification", lambda *_: True)

    restored, created = ReportDeadlineService.create_initial_penalty(
        worker,
        template,
        report_date,
    )

    assert restored.pk == penalty.pk
    assert created is True
    assert restored.status == PenaltyStatus.ACCEPTED
