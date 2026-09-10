"""
Business logic for the Gramly Control bot.
All methods are sync — wrap with sync_to_async in async bot handlers.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional, List

from django.db import transaction
from django.core.exceptions import PermissionDenied
from django.utils import timezone

import datetime as _dt
from zoneinfo import ZoneInfo as _ZoneInfo

from apps.control.deadlines import (
    CORRECTION_WINDOW as _CORRECTION_WINDOW,
    LATE_WINDOW as _LATE_WINDOW,
    calculate_report_deadline,
)

from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)

_MSK = _ZoneInfo("Europe/Moscow")

_RU_MONTHS_GEN = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _format_period_label(d: _dt.date) -> str:
    """Format date as Russian genitive month string, e.g. '7 июля 2026'."""
    return f"{d.day} {_RU_MONTHS_GEN[d.month]} {d.year}"


def _calc_report_date() -> _dt.date:
    """
    Determine which calendar date a report being submitted NOW belongs to.

    Deadline is midnight (00:00 MSK). Workers who miss it and submit in the
    early morning hours (00:00–05:59) are filing a LATE report for the previous
    day — not an early report for the new day.

    Returns yesterday's date for 00:00–05:59 MSK, today's date otherwise.
    """
    now_msk = timezone.now().astimezone(_MSK)
    if now_msk.hour < 6:
        return (now_msk - _dt.timedelta(days=1)).date()
    return now_msk.date()


def _calc_deadline_at(
    template: Optional["ReportTemplate"],
    report_date: _dt.date,
) -> Optional[_dt.datetime]:
    """Combine report_date + template.deadline_time in MSK → UTC-aware datetime."""
    if template is None:
        return None
    return calculate_report_deadline(report_date, template.deadline_time)


def _resolve_report_date(user: User, template: "ReportTemplate", now: _dt.datetime) -> _dt.date:
    """Resolve the one report obligation the worker is currently allowed to edit."""
    from apps.control.models import (
        EmployeeReport, Penalty, PenaltySource,
        REPORT_MODERATION_STATUSES, ReportStatus,
    )

    now_msk = now.astimezone(_MSK)
    today = now_msk.date()
    previous = today - _dt.timedelta(days=1)
    previous_deadline = _calc_deadline_at(template, previous)
    current_deadline = _calc_deadline_at(template, today)

    # Before today's deadline, yesterday may still be inside its fixed 24-hour
    # late window. It takes precedence until it is accepted/finally rejected.
    if (
        previous_deadline
        and previous_deadline <= now < previous_deadline + _LATE_WINDOW
        and current_deadline
        and now < current_deadline
    ):
        prior = EmployeeReport.objects.filter(
            user=user,
            template=template,
            report_date=previous,
        ).first()
        has_previous_obligation = prior is not None or Penalty.objects.filter(
            user=user,
            template=template,
            report_date=previous,
            source=PenaltySource.DEADLINE_MISSED,
        ).exists()
        if has_previous_obligation and (
            prior is None or prior.status in REPORT_MODERATION_STATUSES or prior.can_user_edit(now)
        ):
            return previous
        if prior is not None and prior.status not in (
            ReportStatus.ACCEPTED, ReportStatus.REJECTED, ReportStatus.OVERDUE
        ):
            return previous

    return today


# ── Report services ────────────────────────────────────────────────────────────

class ReportService:

    @staticmethod
    def get_active_templates_for_user(user: User) -> List["ReportTemplate"]:
        """Templates assigned to this worker via M2M (new flow)."""
        from apps.control.models import ReportTemplate
        return list(
            ReportTemplate.objects.filter(assigned_users=user).order_by("name")
        )

    @staticmethod
    def get_template_for_user(user: User) -> Optional["ReportTemplate"]:
        """Return the first template assigned to this user (new M2M or legacy OneToOne)."""
        from apps.control.models import ReportTemplate
        tmpl = ReportTemplate.objects.filter(assigned_users=user).first()
        if tmpl:
            return tmpl
        try:
            return user.report_template
        except Exception:
            return None

    @staticmethod
    def get_template(user: User) -> Optional[str]:
        """Return format instructions for the user's template, or None (legacy compat)."""
        tmpl = ReportService.get_template_for_user(user)
        return tmpl.instructions if tmpl else None

    @staticmethod
    def has_blocking_report(user: User) -> bool:
        """True if the worker has a report in a blocking status."""
        from apps.control.models import EmployeeReport, REPORT_BLOCKING_STATUSES
        return EmployeeReport.objects.filter(
            user=user,
            status__in=REPORT_BLOCKING_STATUSES,
        ).exists()

    @staticmethod
    def get_blocking_report(user: User) -> Optional["EmployeeReport"]:
        """Return the blocking report, if any."""
        from apps.control.models import EmployeeReport, REPORT_BLOCKING_STATUSES
        return (
            EmployeeReport.objects.filter(user=user, status__in=REPORT_BLOCKING_STATUSES)
            .order_by("-submitted_at")
            .first()
        )

    @staticmethod
    def can_moderate(moderator: User, report: "EmployeeReport", workspace=None) -> bool:
        """
        Permission check for moderation actions (accept / reject).

        Rules:
        - Effective reports.moderate permission is mandatory (fresh DB state).
        - Nobody can moderate their own report, including administrators.
        - CURATOR can moderate WORKER/ACCOUNTANT reports only.
        - Other non-admin roles require an explicit permission grant.
        """
        from apps.control.models import REPORT_MODERATION_STATUSES
        from apps.crm.access import has_permission

        # Accepted/rejected reports are terminal until the employee submits an
        # allowed edit. This also protects the POST endpoint from stale pages.
        if report.status not in REPORT_MODERATION_STATUSES:
            return False
        if report.user_id == moderator.pk:
            return False
        if not has_permission(moderator, "reports.moderate", workspace=workspace):
            return False
        # User instances retained by a bot FSM or prefetched report may be stale.
        role = User.objects.filter(pk=moderator.pk).values_list("role", flat=True).first()
        target_role = User.objects.filter(pk=report.user_id).values_list("role", flat=True).first()
        if role == UserRole.CURATOR:
            return target_role in (UserRole.WORKER, UserRole.ACCOUNTANT)
        return target_role is not None

    @staticmethod
    @transaction.atomic
    def submit_report(
        user: User,
        template: Optional["ReportTemplate"] = None,
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
        report_id: Optional[int] = None,
        explicit_report_date: Optional[_dt.date] = None,
    ) -> "EmployeeReport":
        """
        Submit (or resubmit) a report.

        Single-record rule: one EmployeeReport per (user, template, report_date).
        • If no existing record → create new (first submission).
        • If existing record is REJECTED and editing is still allowed → update in-place
          (status → UPDATED, last_submission_at refreshed).
        • If existing record is REJECTED but editing is locked → raise ValueError.
        • Any other blocking status → raise ValueError (already pending/accepted).
        """
        from apps.control.models import (
            EmployeeReport, ModerationHistory, ReportStatus,
            REPORT_BLOCKING_STATUSES,
        )

        now = timezone.now()
        if report_id is not None:
            explicit = EmployeeReport.objects.select_for_update().filter(
                pk=report_id,
                user=user,
            ).first()
            if explicit is None:
                raise ValueError("Отчёт не найден или принадлежит другому сотруднику.")
            if template is not None and explicit.template_id != template.pk:
                raise ValueError("Шаблон отчёта не совпадает.")
            template = explicit.template
            report_date = explicit.report_date
        elif template is not None:
            if explicit_report_date is not None:
                today = now.astimezone(_MSK).date()
                if explicit_report_date not in {today, today - _dt.timedelta(days=1)}:
                    raise ValueError("Для этой даты подача отчёта недоступна.")
                report_date = explicit_report_date
            else:
                report_date = _resolve_report_date(user, template, now)
            explicit = None
        else:
            report_date = _calc_report_date()
            explicit = None

        # A pending report for an earlier date of this same template must be
        # moderated before the next daily obligation can be submitted.
        if template is not None:
            previous_pending = (
                EmployeeReport.objects.filter(
                    user=user,
                    template=template,
                    report_date__lt=report_date,
                    status__in=REPORT_BLOCKING_STATUSES,
                )
                .order_by("-report_date")
                .first()
            )
            if previous_pending is not None:
                raise ValueError(
                    f"Сначала дождитесь решения по отчёту за {previous_pending.period_label}."
                )

        # Look for an existing report for the same user + template + date
        existing = explicit or (
            EmployeeReport.objects.select_for_update()
            .filter(user=user, template=template, report_date=report_date)
            .first()
        )

        if existing is not None:
            if existing.status == ReportStatus.ACCEPTED:
                raise ValueError("Принятый отчёт больше нельзя редактировать.")
            if not existing.can_user_edit(now):
                if existing.status in REPORT_BLOCKING_STATUSES:
                    raise ValueError(
                        "Отчёт уже отправлен на модерацию, а разрешённый период редактирования закончился."
                    )
                raise ValueError("Редактирование заблокировано: разрешённый период закончился.")

            last_cycle = (
                existing.history.order_by("-cycle").values_list("cycle", flat=True).first()
                or 1
            )
            prev_status = existing.status
            existing.status = ReportStatus.UPDATED
            existing.current_revision += 1
            existing.text_content = text
            existing.telegram_file_id = file_id
            existing.file_type = file_type or "text"
            existing.original_filename = original_filename
            existing.last_submission_at = now
            existing.save(update_fields=[
                "status", "text_content", "telegram_file_id",
                "file_type", "original_filename", "current_revision",
                "last_submission_at", "updated_at",
            ])
            ModerationHistory.objects.create(
                report=existing,
                cycle=last_cycle + 1,
                action=ModerationHistory.Action.RESUBMIT,
                moderator=None,
                prev_status=prev_status,
                new_status=ReportStatus.UPDATED,
            )
            return existing

        # First submission — create a new record
        status = ReportStatus.ON_MODERATION if template else ReportStatus.PENDING
        deadline_at = _calc_deadline_at(template, report_date)
        is_late = bool(deadline_at and now >= deadline_at)
        late_window_ends_at = (deadline_at + _LATE_WINDOW) if is_late else None
        if late_window_ends_at and now >= late_window_ends_at:
            raise ValueError("24-часовое окно подачи просроченного отчёта закончилось.")
        editing_locked_at = late_window_ends_at if is_late else deadline_at

        report = EmployeeReport.objects.create(
            user=user,
            template=template,
            status=status,
            text_content=text,
            telegram_file_id=file_id,
            file_type=file_type or "text",
            original_filename=original_filename,
            report_date=report_date,
            period_label=_format_period_label(report_date),
            first_submission_at=now,
            last_submission_at=now,
            deadline_at=deadline_at,
            editing_locked_at=editing_locked_at,
            is_late_submission=is_late,
            late_window_ends_at=late_window_ends_at,
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=1,
            action=ModerationHistory.Action.SUBMIT,
            moderator=None,
            prev_status="",
            new_status=status,
        )
        return report

    @staticmethod
    @transaction.atomic
    def append_to_current_revision(
        report: "EmployeeReport",
        user: User,
        *,
        text: str = "",
    ) -> "EmployeeReport":
        """Append content to an in-progress multi-attachment submission."""
        from apps.control.models import EmployeeReport

        locked = EmployeeReport.objects.select_for_update().get(pk=report.pk, user=user)
        if not locked.can_user_edit():
            raise ValueError("Период редактирования уже закончился.")
        locked.last_submission_at = timezone.now()
        if text:
            locked.text_content = "\n\n".join(
                part for part in (locked.text_content.strip(), text.strip()) if part
            )
            locked.save(update_fields=["text_content", "last_submission_at", "updated_at"])
        else:
            locked.save(update_fields=["last_submission_at", "updated_at"])
        return locked

    @staticmethod
    @transaction.atomic
    def complete_submission(report_id: int, user: User) -> "EmployeeReport":
        """Confirm that the employee finished composing a persisted report."""
        from apps.control.models import EmployeeReport
        from apps.control.tasks import queue_report_submission_notification

        report = EmployeeReport.objects.select_for_update().filter(
            pk=report_id,
            user=user,
        ).first()
        if report is None:
            raise ValueError("Отчёт не найден. Начните подачу заново.")
        if not report.text_content and not report.media_files.filter(
            revision=report.current_revision,
            status="ready",
        ).exists():
            raise ValueError("Отчёт пуст. Отправьте текст или файл.")
        if report.submission_completed_at is None:
            report.submission_completed_at = timezone.now()
            report.save(update_fields=["submission_completed_at", "updated_at"])
            transaction.on_commit(lambda: queue_report_submission_notification(report.pk))
        return report

    @staticmethod
    @transaction.atomic
    def accept_report(report: "EmployeeReport", admin: User, comment: str = "", *, workspace=None) -> None:
        from apps.control.models import (
            EmployeeReport, ModerationHistory, ReportStatus,
            REPORT_MODERATION_STATUSES,
        )

        report = EmployeeReport.objects.select_for_update().get(pk=report.pk)
        if report.status not in REPORT_MODERATION_STATUSES:
            raise ValueError("По этому отчёту уже принято решение.")
        if not ReportService.can_moderate(admin, report, workspace=workspace):
            raise PermissionDenied("Нет права модерировать этот отчёт.")

        prev_status = report.status
        now = timezone.now()

        deadline_met = None
        if report.deadline_at and report.first_submission_at:
            deadline_met = report.first_submission_at <= report.deadline_at

        report.status = ReportStatus.ACCEPTED
        report.reviewed_by = admin
        report.reviewed_at = now
        report.review_comment = comment
        report.correction_deadline = None
        report.deadline_met = deadline_met
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_deadline", "deadline_met", "updated_at",
        ])

        cycle = (
            report.history.order_by("-cycle").values_list("cycle", flat=True).first() or 1
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=cycle,
            action=ModerationHistory.Action.ACCEPT,
            moderator=admin,
            prev_status=prev_status,
            new_status=ReportStatus.ACCEPTED,
            comment=comment,
        )
        from apps.control.tasks import queue_report_decision_notification
        transaction.on_commit(lambda: queue_report_decision_notification(report.pk))

    @staticmethod
    @transaction.atomic
    def reject_report(report: "EmployeeReport", admin: User, comment: str = "", *, workspace=None) -> None:
        """Reject a report and open the configured correction window."""
        from apps.control.models import (
            EmployeeReport, ModerationHistory, ReportStatus,
            REPORT_MODERATION_STATUSES,
        )

        report = EmployeeReport.objects.select_for_update().get(pk=report.pk)
        if report.status not in REPORT_MODERATION_STATUSES:
            raise ValueError("По этому отчёту уже принято решение.")
        if not ReportService.can_moderate(admin, report, workspace=workspace):
            raise PermissionDenied("Нет права модерировать этот отчёт.")

        prev_status = report.status
        now = timezone.now()

        report.status = ReportStatus.REJECTED
        report.reviewed_by = admin
        report.reviewed_at = now
        report.review_comment = comment
        if report.is_late_submission:
            report.correction_deadline = None
            report.editing_locked_at = report.late_window_ends_at
        elif report.deadline_at and now <= report.deadline_at:
            # Rejection before the main deadline does not start a correction
            # hour; the worker may keep editing until the original deadline.
            report.correction_deadline = None
            report.editing_locked_at = report.deadline_at
        else:
            # The first rejection after the deadline starts exactly one hour.
            # Repeated rejections retain the original end timestamp.
            if report.correction_started_at is None:
                report.correction_started_at = now
                report.correction_deadline = now + _CORRECTION_WINDOW
            report.editing_locked_at = report.correction_deadline
        report.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "review_comment",
            "correction_started_at", "correction_deadline",
            "editing_locked_at", "updated_at",
        ])

        cycle = (
            report.history.order_by("-cycle").values_list("cycle", flat=True).first() or 1
        )
        ModerationHistory.objects.create(
            report=report,
            cycle=cycle,
            action=ModerationHistory.Action.REJECT,
            moderator=admin,
            prev_status=prev_status,
            new_status=ReportStatus.REJECTED,
            comment=comment,
        )

        from apps.control.tasks import queue_report_decision_notification
        transaction.on_commit(lambda: queue_report_decision_notification(report.pk))

        if report.is_late_submission and report.late_window_ends_at and now >= report.late_window_ends_at:
            ReportDeadlineService.create_additional_penalty(report)
        elif report.correction_deadline and now >= report.correction_deadline:
            ReportDeadlineService.create_correction_penalty(report)

    @staticmethod
    def resubmit_report(
        original_report: "EmployeeReport",
        text: str = "",
        file_id: str = "",
        file_type: str = "text",
        original_filename: str = "",
    ) -> "EmployeeReport":
        """
        Legacy shim — now delegates to submit_report so the single-record invariant
        is preserved. Kept so old call sites don't break.
        """
        return ReportService.submit_report(
            user=original_report.user,
            template=original_report.template,
            text=text,
            file_id=file_id,
            file_type=file_type,
            original_filename=original_filename,
        )

    @staticmethod
    @transaction.atomic
    def send_to_revision(report: "EmployeeReport", admin: User, comment: str = "", *, workspace=None) -> None:
        """Legacy: send to revision (kept for backward compat)."""
        from apps.control.models import EmployeeReport, ReportStatus, REPORT_MODERATION_STATUSES
        report = EmployeeReport.objects.select_for_update().get(pk=report.pk)
        if report.status not in REPORT_MODERATION_STATUSES:
            raise ValueError("По этому отчёту уже принято решение.")
        if not ReportService.can_moderate(admin, report, workspace=workspace):
            raise PermissionDenied("Нет права модерировать этот отчёт.")
        report.status = ReportStatus.REVISION
        report.reviewed_by = admin
        report.reviewed_at = timezone.now()
        report.review_comment = comment
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment", "updated_at"])

    @staticmethod
    def get_all_pending():
        """All reports awaiting review (any admin)."""
        from apps.control.models import EmployeeReport, REPORT_MODERATION_STATUSES
        return (
            EmployeeReport.objects.filter(status__in=REPORT_MODERATION_STATUSES)
            .select_related("user", "template", "template__created_by")
            .order_by("-submitted_at")
        )

    @staticmethod
    def get_pending_for_admin(admin: User):
        """Reports for templates created by this admin (new routing)."""
        from apps.control.models import EmployeeReport, REPORT_MODERATION_STATUSES
        return (
            EmployeeReport.objects.filter(
                status__in=REPORT_MODERATION_STATUSES,
                template__created_by=admin,
            )
            .select_related("user", "template")
            .order_by("-submitted_at")
        )

    @staticmethod
    def get_reports_for_user(user: User):
        from apps.control.models import EmployeeReport
        return EmployeeReport.objects.filter(user=user).order_by("-submitted_at")[:10]

    @staticmethod
    def mark_overdue_correction_deadlines() -> int:
        """Mark as OVERDUE all REJECTED reports whose editing_locked_at has passed."""
        from apps.control.models import EmployeeReport, ReportStatus
        now = timezone.now()
        qs = EmployeeReport.objects.filter(
            status=ReportStatus.REJECTED,
            editing_locked_at__lt=now,
            editing_locked_at__isnull=False,
        )
        count = qs.count()
        qs.update(status=ReportStatus.OVERDUE, updated_at=now)
        return count


class ReportDeadlineService:
    """Idempotent penalty transitions for one report obligation."""

    @staticmethod
    @transaction.atomic
    def create_penalty(
        *,
        worker: User,
        template: "ReportTemplate",
        report_date: _dt.date,
        source: str,
        reason: str,
        report: Optional["EmployeeReport"] = None,
    ):
        from apps.control.models import Penalty, PenaltyStatus, PenaltyType

        if template.auto_penalty_amount <= 0:
            return None, False
        penalty, created = Penalty.objects.get_or_create(
            user=worker,
            template=template,
            report_date=report_date,
            source=source,
            type=PenaltyType.AUTO,
            defaults={
                "amount": template.auto_penalty_amount,
                "reason": reason,
                "status": PenaltyStatus.ACCEPTED,
                "report": report,
            },
        )
        if report is not None and penalty.report_id is None:
            penalty.report = report
            penalty.save(update_fields=["report", "updated_at"])
        reactivated = False
        if (
            not created
            and penalty.status == PenaltyStatus.REJECTED
            and penalty.comment.startswith("[AUTO-REMEDIATION:PREMATURE]")
        ):
            penalty.status = PenaltyStatus.ACCEPTED
            penalty.amount = template.auto_penalty_amount
            penalty.reason = reason
            penalty.comment = "[AUTO-REMEDIATION:REACTIVATED] Основание наступило после исправленного дедлайна."
            penalty.resolved_by = None
            penalty.resolved_at = None
            penalty.save(update_fields=[
                "status", "amount", "reason", "comment", "resolved_by", "resolved_at", "updated_at",
            ])
            reactivated = True
        if created or reactivated:
            from apps.control.tasks import queue_auto_penalty_notification
            transaction.on_commit(lambda: queue_auto_penalty_notification(penalty.pk))
        return penalty, created or reactivated

    @staticmethod
    def create_initial_penalty(worker, template, report_date, report=None):
        from apps.control.models import PenaltySource

        penalty, created = ReportDeadlineService.create_penalty(
            worker=worker,
            template=template,
            report_date=report_date,
            source=PenaltySource.DEADLINE_MISSED,
            reason=f"Пропущен дедлайн отчёта «{template.name or 'Отчёт'}» ({report_date:%d.%m.%Y})",
            report=report,
        )
        if report is not None and penalty is not None:
            updates = []
            if report.initial_penalty_created_at is None:
                report.initial_penalty_created_at = penalty.created_at
                updates.append("initial_penalty_created_at")
            if not report.is_late_submission:
                report.is_late_submission = True
                updates.append("is_late_submission")
            if report.deadline_at and report.late_window_ends_at is None:
                report.late_window_ends_at = report.deadline_at + _LATE_WINDOW
                report.editing_locked_at = report.late_window_ends_at
                updates.extend(["late_window_ends_at", "editing_locked_at"])
            if updates:
                report.save(update_fields=[*updates, "updated_at"])
        return penalty, created

    @staticmethod
    def create_correction_penalty(report):
        from apps.control.models import PenaltySource

        penalty, created = ReportDeadlineService.create_penalty(
            worker=report.user,
            template=report.template,
            report_date=report.report_date,
            source=PenaltySource.CORRECTION_EXPIRED,
            reason=f"Отчёт «{report.template.name or 'Отчёт'}» не исправлен в срок ({report.report_date:%d.%m.%Y})",
            report=report,
        )
        if penalty is not None and report.additional_penalty_created_at is None:
            report.additional_penalty_created_at = penalty.created_at
            report.save(update_fields=["additional_penalty_created_at", "updated_at"])
        return penalty, created

    @staticmethod
    def create_additional_penalty(report=None, *, worker=None, template=None, report_date=None):
        from apps.control.models import PenaltySource

        if report is not None:
            worker = report.user
            template = report.template
            report_date = report.report_date
        penalty, created = ReportDeadlineService.create_penalty(
            worker=worker,
            template=template,
            report_date=report_date,
            source=PenaltySource.LATE_WINDOW_EXPIRED,
            reason=f"Просроченный отчёт «{template.name or 'Отчёт'}» не принят за 24 часа ({report_date:%d.%m.%Y})",
            report=report,
        )
        if report is not None and penalty is not None and report.additional_penalty_created_at is None:
            report.additional_penalty_created_at = penalty.created_at
            report.save(update_fields=["additional_penalty_created_at", "updated_at"])
        return penalty, created


# ── Penalty services ───────────────────────────────────────────────────────────

class PenaltyService:

    @staticmethod
    @transaction.atomic
    def create_manual(admin: User, worker: User, amount: Decimal,
                      reason: str, comment: str = "", *, workspace=None) -> "Penalty":
        from apps.control.models import Penalty, PenaltyType, PenaltyStatus
        from apps.crm.access import has_permission
        from apps.control.tasks import queue_penalty_notification
        if not has_permission(admin, "penalties.create", workspace=workspace):
            raise PermissionDenied("Нет права предложить штраф.")
        amount = Decimal(amount)
        if not amount.is_finite() or amount <= 0 or amount >= Decimal("10000000000"):
            raise ValueError("Укажите положительную сумму штрафа.")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Сумма должна содержать не более двух знаков после запятой.")
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise ValueError("Укажите причину штрафа (до 500 символов).")
        penalty = Penalty.objects.create(
            user=worker,
            type=PenaltyType.MANUAL,
            amount=amount,
            reason=reason,
            comment=comment,
            status=PenaltyStatus.CREATED,
            created_by=admin,
        )
        transaction.on_commit(lambda: queue_penalty_notification(penalty.pk, expected_status=PenaltyStatus.CREATED))
        return penalty

    @staticmethod
    @transaction.atomic
    def resolve(penalty, actor: User, action: str, *, workspace=None) -> "Penalty":
        """Authorize and resolve a proposal/dispute once, under a row lock.

        The accepted-status ledger already drives available balance; never
        subtract from the employee's stored balance a second time.
        """
        from apps.control.models import Penalty, PenaltyStatus
        from apps.crm.access import has_permission
        from apps.control.tasks import queue_penalty_notification

        locked = Penalty.objects.select_for_update().get(pk=penalty.pk)
        if not has_permission(actor, "penalties.resolve", workspace=workspace):
            raise PermissionDenied("Нет права принимать решения по штрафам.")
        statuses = {"accept": PenaltyStatus.ACCEPTED, "reject": PenaltyStatus.REJECTED,
                    "delete": PenaltyStatus.DELETED}
        if action not in statuses:
            raise ValueError("Неизвестное решение по штрафу.")
        target = statuses[action]
        if locked.status == target:
            return locked
        if locked.status in (PenaltyStatus.REJECTED, PenaltyStatus.DELETED):
            raise ValueError("По этому штрафу уже принято окончательное решение.")
        locked.status = target
        locked.resolved_by = actor
        locked.resolved_at = timezone.now()
        locked.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        if target == PenaltyStatus.ACCEPTED:
            transaction.on_commit(lambda: queue_penalty_notification(locked.pk, expected_status=target))
        return locked

    @staticmethod
    def create_auto(worker: User, report=None, amount: Optional[Decimal] = None,
                    reason: str = "Просрочка подачи отчёта") -> "Penalty":
        from apps.control.models import Penalty, PenaltyType, PenaltyStatus, ControlSettings
        if amount is None:
            settings = ControlSettings.get()
            amount = settings.late_report_penalty_amount
        return Penalty.objects.create(
            user=worker,
            type=PenaltyType.AUTO,
            amount=amount,
            reason=reason,
            status=PenaltyStatus.ACCEPTED,
            report=report,
        )

    @staticmethod
    def get_active_for_user(user: User):
        from apps.control.models import Penalty, PenaltyStatus
        return Penalty.objects.filter(
            user=user,
        ).exclude(status=PenaltyStatus.DELETED).order_by("-created_at")

    @staticmethod
    def dispute(penalty, dispute_comment: str) -> None:
        from apps.control.models import PenaltyStatus
        penalty.status = PenaltyStatus.DISPUTED
        penalty.dispute_comment = dispute_comment
        penalty.save(update_fields=["status", "dispute_comment", "updated_at"])

    @staticmethod
    def get_pending_penalties():
        from apps.control.models import Penalty, PenaltyStatus
        return Penalty.objects.filter(
            status__in=[PenaltyStatus.CREATED, PenaltyStatus.PENDING, PenaltyStatus.DISPUTED]
        ).select_related("user", "created_by", "report", "report__template").order_by("-created_at")

    @staticmethod
    def total_accepted_penalty(user: User) -> Decimal:
        from apps.control.models import Penalty, PenaltyStatus
        from django.db.models import Sum
        result = Penalty.objects.filter(
            user=user, status=PenaltyStatus.ACCEPTED
        ).aggregate(total=Sum("amount"))["total"]
        return result or Decimal("0")


class EmployeeService:
    @staticmethod
    @transaction.atomic
    def archive(user: User, *, actor: User, workspace=None) -> None:
        """Disable an employee while retaining all CRM and financial history."""
        from apps.crm.access import update_user_status
        from apps.crm.identity import terminate_user_sessions
        from apps.crm.models import Workspace
        from apps.users.models import UserStatus

        # Match access-management lock order; a repeated archive is a no-op.
        workspaces = Workspace.objects.select_for_update()
        ws = (workspaces.get(pk=getattr(workspace, "pk", workspace)) if workspace is not None
              else workspaces.get(slug="gramly"))
        previous = User.objects.get(pk=user.pk).status
        update_user_status(actor, user, UserStatus.INACTIVE, workspace=ws)
        if previous != UserStatus.INACTIVE:
            transaction.on_commit(lambda: terminate_user_sessions(user.pk))


# ── Withdrawal services ────────────────────────────────────────────────────────

class ControlWithdrawalService:

    @staticmethod
    @transaction.atomic
    def create(user: User, wallet_address: str, amount: "Decimal | None" = None) -> "WithdrawalRequest":
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalMethod, WithdrawalStatus
        from apps.control.models import ControlSettings
        # Serialize requests per employee so two simultaneous bot callbacks cannot
        # create duplicate pending withdrawals against the same balance.
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        from apps.crm.access import has_permission
        if not has_permission(locked_user, "personal.use"):
            raise PermissionDenied("Личные операции недоступны.")
        available = ControlBalanceService.get_available_balance(locked_user)
        if amount is None:
            amount = available
        if amount <= 0 or available <= 0:
            raise ValueError("Недостаточно средств для вывода")
        if amount > available:
            raise ValueError(f"Сумма превышает доступный баланс ({available:.2f} ₽)")
        settings = ControlSettings.get()
        if amount < settings.min_withdrawal_amount:
            raise ValueError(f"Минимальная сумма вывода — {settings.min_withdrawal_amount:.0f} ₽")
        if ReportService.has_blocking_report(locked_user):
            raise ValueError("Вывод заблокирован: отчёт ожидает проверки")
        existing = WithdrawalRequest.objects.filter(
            user=locked_user, status=WithdrawalStatus.PENDING
        ).first()
        if existing:
            raise ValueError("У вас уже есть активная заявка на вывод")
        return WithdrawalRequest.objects.create(
            user=locked_user,
            amount=amount,
            method=WithdrawalMethod.USDT_TRC20,
            details=wallet_address,
            status=WithdrawalStatus.PENDING,
        )

    @staticmethod
    def get_processor_ids() -> list:
        """Return telegram_ids of all users who should receive withdrawal notifications."""
        from apps.users.models import UserRole, UserStatus
        from apps.control.models import ControlSettings
        from apps.crm.access import has_permission
        accountants = list(
            User.objects.filter(
                role=UserRole.ACCOUNTANT,
                status=UserStatus.ACTIVE,
                is_blocked_bot=False,
            )
        )
        settings = ControlSettings.get()
        extras = list(
            settings.withdrawal_processors.filter(
                status=UserStatus.ACTIVE,
                is_blocked_bot=False,
            )
        )
        seen = set()
        result = []
        for user in accountants + extras:
            if user.telegram_id not in seen and has_permission(user, "withdrawals.view"):
                seen.add(user.telegram_id)
                result.append(user.telegram_id)
        return result

    @staticmethod
    def get_saved_addresses(user: User) -> list:
        from apps.withdrawals.models import CryptoAddress
        return list(CryptoAddress.objects.filter(user=user))

    @staticmethod
    @transaction.atomic
    def save_address(user: User, name: str, address: str) -> "CryptoAddress":
        from apps.withdrawals.models import CryptoAddress
        from apps.crm.access import has_permission
        user = User.objects.select_for_update().get(pk=user.pk)
        if not has_permission(user, "personal.use"):
            raise PermissionDenied("Личные операции недоступны.")
        return CryptoAddress.objects.create(user=user, name=name, address=address)

    @staticmethod
    @transaction.atomic
    def delete_address(user: User, address_id: int) -> bool:
        from apps.withdrawals.models import CryptoAddress
        from apps.crm.access import has_permission
        user = User.objects.select_for_update().get(pk=user.pk)
        if not has_permission(user, "personal.use"):
            raise PermissionDenied("Личные операции недоступны.")
        deleted, _ = CryptoAddress.objects.filter(pk=address_id, user=user).delete()
        return deleted > 0

    @staticmethod
    def _notify_sync(telegram_id: int, text: str) -> None:
        """Send a Telegram message synchronously (for use from sync Django views / Celery)."""
        import asyncio
        from aiogram import Bot
        from django.conf import settings

        async def _send():
            # A handler's singleton session belongs to another event loop.
            bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
            try:
                await bot.send_message(telegram_id, text, parse_mode="HTML")
            except Exception:
                pass
            finally:
                await bot.session.close()

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_send())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    @staticmethod
    @transaction.atomic
    def _transition(withdrawal, actor: User, target: str, *, workspace=None):
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
        from apps.crm.access import has_permission

        locked = WithdrawalRequest.objects.select_for_update().select_related("user").get(pk=withdrawal.pk)
        if not has_permission(actor, "withdrawals.manage", workspace=workspace):
            raise PermissionDenied("Нет права обрабатывать выводы.")
        role = User.objects.filter(pk=actor.pk).values_list("role", flat=True).first()
        if locked.user_id == actor.pk and role != UserRole.ACCOUNTANT:
            raise PermissionDenied("Нельзя обработать собственную заявку на вывод.")
        if locked.status == target:
            return locked
        if locked.status in (WithdrawalStatus.APPROVED, WithdrawalStatus.REJECTED):
            raise ValueError("Заявка уже завершена.")
        if target == WithdrawalStatus.PROCESSING and locked.status != WithdrawalStatus.PENDING:
            raise ValueError("Нельзя вернуть заявку на предыдущий этап.")
        locked.status = target
        locked.processed_by = actor
        fields = ["status", "processed_by", "updated_at"]
        if target != WithdrawalStatus.PROCESSING:
            locked.processed_at = timezone.now()
            fields.append("processed_at")
        locked.save(update_fields=fields)
        messages = {
            WithdrawalStatus.RECEIPT_SENT: (
                f"📤 <b>Чек на вывод отправлен</b>\n\n"
                f"Заявка #{locked.pk} на <b>{locked.amount:.2f} ₽</b> — чек отправлен. "
                "Проверьте ваш кошелёк."
            ),
            WithdrawalStatus.APPROVED: (
                f"✅ <b>Вывод выполнен</b>\n\n"
                f"Заявка #{locked.pk} на <b>{locked.amount:.2f} ₽</b> успешно выполнена."
            ),
            WithdrawalStatus.REJECTED: (
                f"❌ <b>Заявка на вывод отклонена</b>\n\n"
                f"Заявка #{locked.pk} на <b>{locked.amount:.2f} ₽</b> отклонена."
            ),
        }
        if target in messages:
            text = messages[target]
            transaction.on_commit(lambda: ControlWithdrawalService._notify_sync(locked.user.telegram_id, text))
        return locked

    @staticmethod
    def mark_processing(withdrawal, accountant: User, *, workspace=None):
        from apps.withdrawals.models import WithdrawalStatus
        return ControlWithdrawalService._transition(withdrawal, accountant, WithdrawalStatus.PROCESSING, workspace=workspace)

    @staticmethod
    def mark_receipt_sent(withdrawal, accountant: User, *, workspace=None):
        from apps.withdrawals.models import WithdrawalStatus
        return ControlWithdrawalService._transition(withdrawal, accountant, WithdrawalStatus.RECEIPT_SENT, workspace=workspace)

    @staticmethod
    def mark_completed(withdrawal, accountant: User, *, workspace=None):
        from apps.withdrawals.models import WithdrawalStatus
        return ControlWithdrawalService._transition(withdrawal, accountant, WithdrawalStatus.APPROVED, workspace=workspace)

    @staticmethod
    def reject(withdrawal, accountant: User, *, workspace=None):
        from apps.withdrawals.models import WithdrawalStatus
        return ControlWithdrawalService._transition(withdrawal, accountant, WithdrawalStatus.REJECTED, workspace=workspace)

    @staticmethod
    def get_pending_list():
        from apps.withdrawals.models import WithdrawalRequest, WithdrawalStatus
        return WithdrawalRequest.objects.filter(
            status__in=[WithdrawalStatus.PENDING, WithdrawalStatus.PROCESSING, WithdrawalStatus.RECEIPT_SENT]
        ).select_related("user").order_by("created_at")


# ── KPI services ───────────────────────────────────────────────────────────────

class KPIService:

    @staticmethod
    def get_or_create_settings(user: User) -> "KPISettings":
        from apps.control.models import KPISettings
        obj, _ = KPISettings.objects.get_or_create(user=user)
        return obj

    @staticmethod
    def update_settings(user: User, admin: User, base_rate: Decimal,
                        bonus_rate: Decimal, penalty_rate: Decimal,
                        other_info: str = "") -> "KPISettings":
        from apps.control.models import KPISettings
        obj, _ = KPISettings.objects.get_or_create(user=user)
        obj.base_rate = base_rate
        obj.bonus_rate = bonus_rate
        obj.penalty_rate = penalty_rate
        obj.other_info = other_info
        obj.updated_by = admin
        obj.save()
        return obj

    @staticmethod
    def get_document(user: User) -> Optional["KPIDocument"]:
        try:
            return user.kpi_document
        except Exception:
            return None


# ── Balance calculation for Control bot ──────────────────────────────────────

class ControlBalanceService:

    @staticmethod
    def get_balance_snapshot(user: User) -> dict[str, Decimal]:
        """Return every balance component from the ledger at one point in time."""
        personal = user.compute_personal_earned()
        referral = user.compute_referral_earned()
        daily = user.daily_accrued or Decimal("0")
        adjustment = user.manual_balance_adjustment or Decimal("0")
        withdrawn = user.compute_withdrawn()
        penalties = PenaltyService.total_accepted_penalty(user)
        gross = personal + referral + daily + adjustment

        return {
            "personal": personal,
            "referral": referral,
            "daily": daily,
            "adjustment": adjustment,
            "gross": gross,
            "withdrawn": withdrawn,
            "penalties": penalties,
            "available": max(Decimal("0"), gross - withdrawn - penalties),
        }

    @staticmethod
    def get_total_balance(user: User) -> Decimal:
        return (
            user.compute_personal_earned()
            + user.compute_referral_earned()
            + (user.daily_accrued or Decimal("0"))
            + (user.manual_balance_adjustment or Decimal("0"))
        )

    @staticmethod
    def get_available_balance(user: User) -> Decimal:
        return ControlBalanceService.get_balance_snapshot(user)["available"]

    @staticmethod
    def _set_available_balance_locked(user: User, amount: Decimal) -> Decimal:
        """Set a target balance on an already row-locked User instance."""
        amount = Decimal(amount).quantize(Decimal("0.01"))
        if amount < 0:
            raise ValueError("Баланс не может быть отрицательным")
        base_gross = (
            user.compute_personal_earned()
            + user.compute_referral_earned()
            + (user.daily_accrued or Decimal("0"))
        )
        withdrawn = user.compute_withdrawn()
        penalties = PenaltyService.total_accepted_penalty(user)
        user.manual_balance_adjustment = amount + withdrawn + penalties - base_gross
        user.save(update_fields=["manual_balance_adjustment", "updated_at"])
        return amount

    @staticmethod
    @transaction.atomic
    def set_available_balance(user: User, amount: Decimal) -> Decimal:
        """
        Set the amount that the bot must show as available right now.

        Withdrawals, penalties and earned daily rates are historical facts and
        must not be overwritten.  Store only the balancing adjustment needed to
        reach the requested available amount.
        """
        amount = Decimal(amount).quantize(Decimal("0.01"))
        if amount < 0:
            raise ValueError("Баланс не может быть отрицательным")

        locked_user = User.objects.select_for_update().get(pk=user.pk)
        ControlBalanceService._set_available_balance_locked(locked_user, amount)

        # Keep the caller usable in the same request without a refresh.
        user.manual_balance_adjustment = locked_user.manual_balance_adjustment
        return amount


class FinancialConditionService:
    """Atomic admin updates with a durable post-commit notification outbox."""

    @staticmethod
    @transaction.atomic
    def update(
        *,
        worker_id: int,
        admin: User,
        daily_rate: Decimal | None,
        available_balance: Decimal | None,
        workspace=None,
    ):
        from apps.control.models import FinancialConditionChange

        worker = User.objects.select_for_update().get(pk=worker_id)
        from apps.crm.access import has_permission
        if not has_permission(admin, "employees.finance", workspace=workspace):
            raise PermissionDenied("Нет права изменять финансовые условия.")
        old_rate = (worker.daily_rate or Decimal("0")).quantize(Decimal("0.01"))
        old_available = ControlBalanceService.get_available_balance(worker).quantize(Decimal("0.01"))
        daily_rate = old_rate if daily_rate is None else Decimal(daily_rate)
        available_balance = old_available if available_balance is None else Decimal(available_balance)
        if not daily_rate.is_finite() or not available_balance.is_finite():
            raise ValueError("Введите конечное числовое значение")
        daily_rate = daily_rate.quantize(Decimal("0.01"))
        available_balance = available_balance.quantize(Decimal("0.01"))
        if daily_rate < 0:
            raise ValueError("Ставка не может быть отрицательной")
        if available_balance < 0:
            raise ValueError("Баланс не может быть отрицательным")

        if old_rate == daily_rate and old_available == available_balance:
            return worker, None

        if old_rate != daily_rate:
            worker.daily_rate = daily_rate
            worker.save(update_fields=["daily_rate", "updated_at"])
        if old_available != available_balance:
            ControlBalanceService._set_available_balance_locked(worker, available_balance)

        new_available = ControlBalanceService.get_available_balance(worker).quantize(Decimal("0.01"))
        event = FinancialConditionChange.objects.create(
            worker=worker,
            changed_by=admin,
            previous_daily_rate=old_rate,
            new_daily_rate=daily_rate,
            previous_available_balance=old_available,
            new_available_balance=new_available,
        )
        from apps.control.tasks import queue_financial_condition_notification
        transaction.on_commit(lambda: queue_financial_condition_notification(event.pk))
        return worker, event


# ── Worker list helper ─────────────────────────────────────────────────────────

def get_all_workers():
    return User.objects.filter(role=UserRole.WORKER, status="active").order_by("telegram_username")


def get_all_accountants():
    return User.objects.filter(role=UserRole.ACCOUNTANT, status="active")
