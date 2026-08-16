"""
Report lifecycle tests — 30 scenarios.

Covers: submit, resubmit, editing lock, accept, reject, deadline_met,
permissions (can_moderate), and ModerationHistory audit trail.
"""
import datetime
import pytest
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.users.models import User, UserRole, UserStatus
from apps.control.models import (
    ReportTemplate, EmployeeReport, ModerationHistory,
    ReportStatus, REPORT_BLOCKING_STATUSES,
)
from apps.control.services import ReportService

_MSK = ZoneInfo("Europe/Moscow")

pytestmark = pytest.mark.django_db


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _user(tg_id, role=UserRole.WORKER, username=None):
    return User.objects.create(
        telegram_id=tg_id,
        role=role,
        status=UserStatus.ACTIVE,
        telegram_username=username or f"user{tg_id}",
    )


def _template(deadline_time=datetime.time(23, 0), correction_hours=24):
    return ReportTemplate.objects.create(
        name="Test Template",
        deadline_time=deadline_time,
        correction_deadline_hours=correction_hours,
    )


def _deadline_at(template, report_date):
    naive = datetime.datetime.combine(report_date, template.deadline_time)
    return naive.replace(tzinfo=_MSK)


# ── 1. First submission creates a new record ───────────────────────────────────

def test_first_submit_creates_record(monkeypatch):
    worker = _user(1001)
    tmpl = _template()
    now = datetime.datetime(2026, 8, 16, 20, 0, tzinfo=_MSK)
    monkeypatch.setattr("apps.control.services.timezone.now", lambda: now)
    report = ReportService.submit_report(worker, template=tmpl, text="Report text")
    assert report.pk is not None
    assert report.status == ReportStatus.ON_MODERATION
    assert report.first_submission_at is not None
    assert report.last_submission_at is not None
    assert report.deadline_at is not None
    assert report.editing_locked_at == report.deadline_at


# ── 2. First submission without template → PENDING ─────────────────────────────

def test_first_submit_no_template():
    worker = _user(1002)
    report = ReportService.submit_report(worker, template=None, text="Legacy")
    assert report.status == ReportStatus.PENDING
    assert report.deadline_at is None
    assert report.editing_locked_at is None


# ── 3. First submission records SUBMIT history ─────────────────────────────────

def test_first_submit_creates_history():
    worker = _user(1003)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    history = list(report.history.all())
    assert len(history) == 1
    assert history[0].action == ModerationHistory.Action.SUBMIT
    assert history[0].cycle == 1
    assert history[0].moderator is None
    assert history[0].new_status == ReportStatus.ON_MODERATION


# ── 4. Double-submit with blocking status raises ValueError ────────────────────

def test_repeat_submit_before_deadline_updates_same_report(monkeypatch):
    worker = _user(1004)
    tmpl = _template()
    now = datetime.datetime(2026, 8, 16, 20, 0, tzinfo=_MSK)
    monkeypatch.setattr("apps.control.services.timezone.now", lambda: now)
    first = ReportService.submit_report(worker, template=tmpl, text="first")
    second = ReportService.submit_report(worker, template=tmpl, text="second")
    assert second.pk == first.pk
    assert second.text_content == "second"


# ── 5. Resubmit after rejection updates record in-place ───────────────────────

def test_resubmit_after_rejection_updates_record():
    admin = _user(9001, UserRole.ADMIN)
    worker = _user(1005)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="original")
    ReportService.reject_report(report, admin, "Fix it")

    updated = ReportService.submit_report(worker, template=tmpl, text="corrected")
    assert updated.pk == report.pk
    assert updated.status == ReportStatus.UPDATED
    assert updated.text_content == "corrected"


# ── 6. Resubmit increments cycle and records RESUBMIT history ─────────────────

def test_resubmit_increments_cycle():
    admin = _user(9002, UserRole.ADMIN)
    worker = _user(1006)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="v1")
    ReportService.reject_report(report, admin, "bad")
    ReportService.submit_report(worker, template=tmpl, text="v2")

    history = list(report.history.all())
    submit_h = [h for h in history if h.action == ModerationHistory.Action.SUBMIT]
    resubmit_h = [h for h in history if h.action == ModerationHistory.Action.RESUBMIT]
    assert len(submit_h) == 1
    assert len(resubmit_h) == 1
    assert resubmit_h[0].cycle == 2
    assert resubmit_h[0].prev_status == ReportStatus.REJECTED
    assert resubmit_h[0].new_status == ReportStatus.UPDATED


# ── 7. Resubmit updates last_submission_at ────────────────────────────────────

def test_resubmit_updates_last_submission_at():
    admin = _user(9003, UserRole.ADMIN)
    worker = _user(1007)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="v1")
    first_ts = report.first_submission_at
    ReportService.reject_report(report, admin, "redo")
    ReportService.submit_report(worker, template=tmpl, text="v2")
    report.refresh_from_db()
    assert report.last_submission_at >= report.first_submission_at
    assert report.first_submission_at == first_ts


# ── 8. Accept sets ACCEPTED and writes history ─────────────────────────────────

def test_accept_sets_accepted():
    admin = _user(9004, UserRole.ADMIN)
    worker = _user(1008)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="good")
    ReportService.accept_report(report, admin, "Looks good")
    report.refresh_from_db()
    assert report.status == ReportStatus.ACCEPTED
    assert report.reviewed_by_id == admin.pk
    history = list(report.history.all())
    accept_h = [h for h in history if h.action == ModerationHistory.Action.ACCEPT]
    assert len(accept_h) == 1
    assert accept_h[0].moderator_id == admin.pk
    assert accept_h[0].comment == "Looks good"


# ── 9. Reject sets REJECTED and writes history ─────────────────────────────────

def test_reject_sets_rejected():
    admin = _user(9005, UserRole.ADMIN)
    worker = _user(1009)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="bad")
    ReportService.reject_report(report, admin, "Wrong format")
    report.refresh_from_db()
    assert report.status == ReportStatus.REJECTED
    history = list(report.history.all())
    reject_h = [h for h in history if h.action == ModerationHistory.Action.REJECT]
    assert len(reject_h) == 1
    assert reject_h[0].comment == "Wrong format"


# ── 10. Accept computes deadline_met = True when on time ──────────────────────

def test_deadline_met_true_when_on_time():
    admin = _user(9006, UserRole.ADMIN)
    worker = _user(1010)
    tmpl = _template(deadline_time=datetime.time(23, 0))
    report = ReportService.submit_report(worker, template=tmpl, text="on time")

    # Manually set submission before deadline
    deadline = report.deadline_at
    report.first_submission_at = deadline - datetime.timedelta(hours=2)
    report.last_submission_at = deadline - datetime.timedelta(hours=1)
    report.save(update_fields=["first_submission_at", "last_submission_at"])

    ReportService.accept_report(report, admin)
    report.refresh_from_db()
    assert report.deadline_met is True


# ── 11. Accept computes deadline_met = False when late ────────────────────────

def test_deadline_met_false_when_late():
    admin = _user(9007, UserRole.ADMIN)
    worker = _user(1011)
    tmpl = _template(deadline_time=datetime.time(23, 0))
    report = ReportService.submit_report(worker, template=tmpl, text="late")

    deadline = report.deadline_at
    report.first_submission_at = deadline + datetime.timedelta(minutes=5)
    report.last_submission_at = deadline + datetime.timedelta(minutes=5)
    report.save(update_fields=["first_submission_at", "last_submission_at"])

    ReportService.accept_report(report, admin)
    report.refresh_from_db()
    assert report.deadline_met is False


# ── 12. deadline_met = None when no deadline_at ───────────────────────────────

def test_deadline_met_none_without_deadline():
    admin = _user(9008, UserRole.ADMIN)
    worker = _user(1012)
    report = ReportService.submit_report(worker, template=None, text="no tmpl")
    ReportService.accept_report(report, admin)
    report.refresh_from_db()
    assert report.deadline_met is None


# ── 13. editing_locked_at = deadline_at + 1 hour ─────────────────────────────

def test_editing_locked_at_is_main_deadline_before_rejection(monkeypatch):
    worker = _user(1013)
    tmpl = _template(deadline_time=datetime.time(22, 30))
    now = datetime.datetime(2026, 8, 16, 20, 0, tzinfo=_MSK)
    monkeypatch.setattr("apps.control.services.timezone.now", lambda: now)
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    assert report.editing_locked_at == report.deadline_at


# ── 14. can_user_edit() True when before editing_locked_at ───────────────────

def test_can_user_edit_before_lock():
    admin = _user(9009, UserRole.ADMIN)
    worker = _user(1014)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    ReportService.reject_report(report, admin, "bad")
    report.refresh_from_db()

    # Lock is far in the future (just created with today's deadline)
    # Manually push editing_locked_at to the future
    report.editing_locked_at = timezone.now() + datetime.timedelta(hours=2)
    report.save(update_fields=["editing_locked_at"])
    assert report.can_user_edit() is True


# ── 15. can_user_edit() False when after editing_locked_at ───────────────────

def test_can_user_edit_after_lock():
    admin = _user(9010, UserRole.ADMIN)
    worker = _user(1015)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    ReportService.reject_report(report, admin, "bad")
    report.refresh_from_db()

    report.editing_locked_at = timezone.now() - datetime.timedelta(minutes=5)
    report.save(update_fields=["editing_locked_at"])
    assert report.can_user_edit() is False


# ── 16. Resubmit after lock raises ValueError ─────────────────────────────────

def test_resubmit_after_lock_raises():
    admin = _user(9011, UserRole.ADMIN)
    worker = _user(1016)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    ReportService.reject_report(report, admin, "bad")

    report.editing_locked_at = timezone.now() - datetime.timedelta(minutes=5)
    report.save(update_fields=["editing_locked_at"])

    with pytest.raises(ValueError, match="заблокировано"):
        ReportService.submit_report(worker, template=tmpl, text="late fix")


# ── 17. can_moderate: admin can moderate worker ───────────────────────────────

def test_can_moderate_admin_worker():
    admin = _user(9012, UserRole.ADMIN)
    worker = _user(1017)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    assert ReportService.can_moderate(admin, report) is True


# ── 18. can_moderate: admin cannot moderate own report ────────────────────────

def test_can_moderate_admin_own_report():
    admin = _user(9013, UserRole.ADMIN)
    tmpl = _template()
    report = ReportService.submit_report(admin, template=tmpl, text="x")
    assert ReportService.can_moderate(admin, report) is False


# ── 19. can_moderate: curator can moderate worker ─────────────────────────────

def test_can_moderate_curator_worker():
    curator = _user(9014, UserRole.CURATOR)
    worker = _user(1018)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    assert ReportService.can_moderate(curator, report) is True


# ── 20. can_moderate: curator can moderate accountant ────────────────────────

def test_can_moderate_curator_accountant():
    curator = _user(9015, UserRole.CURATOR)
    accountant = _user(1019, UserRole.ACCOUNTANT)
    tmpl = _template()
    report = ReportService.submit_report(accountant, template=tmpl, text="x")
    assert ReportService.can_moderate(curator, report) is True


# ── 21. can_moderate: curator cannot moderate other curator ───────────────────

def test_can_moderate_curator_cannot_moderate_curator():
    curator1 = _user(9016, UserRole.CURATOR)
    curator2 = _user(9017, UserRole.CURATOR)
    tmpl = _template()
    report = ReportService.submit_report(curator2, template=tmpl, text="x")
    assert ReportService.can_moderate(curator1, report) is False


# ── 22. can_moderate: curator cannot moderate admin ──────────────────────────

def test_can_moderate_curator_cannot_moderate_admin():
    curator = _user(9018, UserRole.CURATOR)
    admin = _user(9019, UserRole.ADMIN)
    tmpl = _template()
    report = ReportService.submit_report(admin, template=tmpl, text="x")
    assert ReportService.can_moderate(curator, report) is False


# ── 23. can_moderate: worker cannot moderate anyone ──────────────────────────

def test_can_moderate_worker_cannot():
    worker1 = _user(1020)
    worker2 = _user(1021)
    tmpl = _template()
    report = ReportService.submit_report(worker2, template=tmpl, text="x")
    assert ReportService.can_moderate(worker1, report) is False


# ── 24. UPDATED status is in REPORT_BLOCKING_STATUSES ────────────────────────

def test_updated_status_is_blocking():
    assert ReportStatus.UPDATED in REPORT_BLOCKING_STATUSES


# ── 25. Accept after resubmit → correct cycle in history ─────────────────────

def test_accept_after_resubmit_correct_cycle():
    admin = _user(9020, UserRole.ADMIN)
    worker = _user(1022)
    tmpl = _template()

    report = ReportService.submit_report(worker, template=tmpl, text="v1")
    ReportService.reject_report(report, admin, "redo")
    ReportService.submit_report(worker, template=tmpl, text="v2")
    report.refresh_from_db()
    ReportService.accept_report(report, admin, "ok")

    history = list(report.history.all())
    accept_h = [h for h in history if h.action == ModerationHistory.Action.ACCEPT]
    assert len(accept_h) == 1
    assert accept_h[0].cycle == 2


# ── 26. period_label is set from report_date ─────────────────────────────────

def test_period_label_set():
    worker = _user(1023)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    assert report.period_label != ""
    assert str(report.report_date.year) in report.period_label


# ── 27. Accepted report cannot be resubmitted ────────────────────────────────

def test_accepted_report_cannot_be_resubmitted():
    admin = _user(9021, UserRole.ADMIN)
    worker = _user(1024)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    ReportService.accept_report(report, admin)
    with pytest.raises(ValueError):
        ReportService.submit_report(worker, template=tmpl, text="again")


# ── 28. mark_overdue_correction_deadlines moves locked REJECTED → OVERDUE ─────

def test_mark_overdue():
    admin = _user(9022, UserRole.ADMIN)
    worker = _user(1025)
    tmpl = _template()
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    ReportService.reject_report(report, admin, "bad")
    report.refresh_from_db()

    # Simulate editing_locked_at in the past
    report.editing_locked_at = timezone.now() - datetime.timedelta(hours=2)
    report.save(update_fields=["editing_locked_at"])

    count = ReportService.mark_overdue_correction_deadlines()
    assert count >= 1
    report.refresh_from_db()
    assert report.status == ReportStatus.OVERDUE


# ── 29. deadline_at is calculated from template.deadline_time + report_date ───

def test_deadline_at_calculation():
    worker = _user(1026)
    tmpl = _template(deadline_time=datetime.time(22, 30))
    report = ReportService.submit_report(worker, template=tmpl, text="x")
    expected_naive = datetime.datetime.combine(report.report_date, datetime.time(22, 30))
    expected = expected_naive.replace(tzinfo=_MSK)
    assert report.deadline_at == expected


# ── 30. Two different templates same user same day → two separate records ──────

def test_two_templates_different_records():
    worker = _user(1027)
    tmpl1 = _template()
    tmpl2 = ReportTemplate.objects.create(name="Template 2", deadline_time=datetime.time(20, 0))

    r1 = ReportService.submit_report(worker, template=tmpl1, text="report1")
    r2 = ReportService.submit_report(worker, template=tmpl2, text="report2")

    assert r1.pk != r2.pk
    assert r1.template_id == tmpl1.pk
    assert r2.template_id == tmpl2.pk
