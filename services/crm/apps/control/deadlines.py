"""Shared report deadline primitives.

The reporting date names the working day.  A deadline at exactly 00:00 is the
end boundary of that day (midnight at the start of the following day), not its
start.  Keeping this rule here prevents the bot, Celery and admin UI from
silently disagreeing.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from django.db.models import Q, Sum
from django.utils import timezone


MSK = ZoneInfo("Europe/Moscow")
LATE_WINDOW = dt.timedelta(hours=24)
CORRECTION_WINDOW = dt.timedelta(hours=1)


def calculate_report_deadline(
    report_date: dt.date | None,
    deadline_time: dt.time | None,
) -> dt.datetime | None:
    """Return the canonical aware deadline for one reporting-day obligation."""
    if report_date is None or deadline_time is None:
        return None
    deadline_date = report_date
    if deadline_time.hour == 0 and deadline_time.minute == 0 and deadline_time.second == 0:
        deadline_date += dt.timedelta(days=1)
    naive = dt.datetime.combine(deadline_date, deadline_time.replace(tzinfo=None))
    return naive.replace(tzinfo=MSK)


def effective_report_deadline(report, template=None) -> dt.datetime | None:
    """Use an immutable stored deadline, correcting only the known midnight bug."""
    stored = getattr(report, "deadline_at", None)
    report_date = getattr(report, "report_date", None)
    if stored is not None and report_date is not None:
        local = timezone.localtime(stored, MSK)
        # Legacy rows stored 00:00 on the reporting date itself.  That exact
        # shape can only have been produced by the old broken calculation.
        if local.date() == report_date and local.time().replace(tzinfo=None) == dt.time(0, 0):
            return calculate_report_deadline(report_date, dt.time(0, 0))
        return stored
    template = template or getattr(report, "template", None)
    return calculate_report_deadline(report_date, getattr(template, "deadline_time", None))


class DeadlineFilter(StrEnum):
    TODAY = "today"
    TOMORROW = "tomorrow"
    WEEK = "week"
    OVERDUE = "overdue"
    ALL = "all"


class DeadlineState(StrEnum):
    SOON = "soon"
    MODERATION = "moderation"
    REJECTED = "rejected"
    OVERDUE = "overdue"


@dataclass(frozen=True, slots=True)
class DeadlineItem:
    template_id: int
    report_id: int | None
    report_date: dt.date
    title: str
    deadline_at: dt.datetime
    state: DeadlineState
    editing_ends_at: dt.datetime | None
    charged_penalty: Decimal
    potential_penalty: Decimal
    can_edit: bool
    can_open: bool

    @property
    def key(self) -> str:
        return f"{self.template_id}:{self.report_date:%Y%m%d}"

    @property
    def status_label(self) -> str:
        return {
            DeadlineState.SOON: "🟠 Скоро",
            DeadlineState.MODERATION: "🟡 На модерации",
            DeadlineState.REJECTED: "🟠 Отклонён",
            DeadlineState.OVERDUE: "🔴 Просрочен",
        }[self.state]


class ReportDeadlineProvider:
    """Build universal deadline DTOs from report-domain data without duplicating it."""

    @staticmethod
    def _state(report, deadline_at: dt.datetime, now: dt.datetime) -> DeadlineState:
        from apps.control.models import REPORT_MODERATION_STATUSES, ReportStatus

        if report is not None and report.status in REPORT_MODERATION_STATUSES:
            return DeadlineState.MODERATION
        if report is not None and report.status == ReportStatus.REJECTED:
            return DeadlineState.REJECTED if report.can_user_edit(now) else DeadlineState.OVERDUE
        if report is not None and report.status == ReportStatus.OVERDUE:
            return DeadlineState.OVERDUE
        return DeadlineState.SOON if now < deadline_at else DeadlineState.OVERDUE

    @classmethod
    def list_for_user(
        cls,
        user,
        selected_filter: DeadlineFilter | str = DeadlineFilter.ALL,
        *,
        now: dt.datetime | None = None,
    ) -> list[DeadlineItem]:
        from apps.control.models import (
            REPORT_MODERATION_STATUSES,
            EmployeeReport,
            Penalty,
            PenaltySource,
            PenaltyStatus,
            ReportStatus,
            ReportTemplate,
        )

        now = now or timezone.now()
        now_msk = now.astimezone(MSK)
        today = now_msk.date()
        horizon_end = today + dt.timedelta(days=6)
        selected_filter = DeadlineFilter(selected_filter)

        templates = list(ReportTemplate.objects.filter(assigned_users=user).order_by("name"))
        if not templates:
            return []
        template_by_id = {template.pk: template for template in templates}
        template_ids = list(template_by_id)

        active_reports = list(
            EmployeeReport.objects.filter(
                user=user,
                template_id__in=template_ids,
                report_date__isnull=False,
                report_date__lte=horizon_end,
            ).filter(
                # Accepted reports are needed only to suppress synthesized
                # obligations/penalties in the same bounded history window.
                Q(report_date__gte=today - dt.timedelta(days=31)) | ~Q(status=ReportStatus.ACCEPTED)
            ).select_related("template")
        )
        report_by_key = {(report.template_id, report.report_date): report for report in active_reports}

        active_penalties = list(
            Penalty.objects.filter(
                user=user,
                template_id__in=template_ids,
                report_date__isnull=False,
                report_date__gte=today - dt.timedelta(days=31),
                type="auto",
                status=PenaltyStatus.ACCEPTED,
            ).values("template_id", "report_date", "source").annotate(total=Sum("amount"))
        )
        penalties_by_key: dict[tuple[int, dt.date], dict[str, Decimal]] = {}
        for row in active_penalties:
            penalties_by_key.setdefault((row["template_id"], row["report_date"]), {})[
                row["source"]
            ] = row["total"] or Decimal("0")

        keys: set[tuple[int, dt.date]] = set(report_by_key) | set(penalties_by_key)
        for template in templates:
            for offset in range(-1, 7):
                keys.add((template.pk, today + dt.timedelta(days=offset)))

        items: list[DeadlineItem] = []
        for template_id, report_date in keys:
            template = template_by_id.get(template_id)
            if template is None or template.deadline_time is None:
                continue
            report = report_by_key.get((template_id, report_date))
            if report is not None and report.status == ReportStatus.ACCEPTED:
                continue
            deadline_at = effective_report_deadline(report, template) if report else calculate_report_deadline(
                report_date, template.deadline_time,
            )
            if deadline_at is None:
                continue
            state = cls._state(report, deadline_at, now)
            penalties = penalties_by_key.get((template_id, report_date), {})
            charged = sum(penalties.values(), Decimal("0"))
            potential = Decimal("0")
            editing_end = None
            if report is not None:
                editing_end = report.editing_locked_at
                if report.status == ReportStatus.REJECTED and PenaltySource.CORRECTION_EXPIRED not in penalties:
                    potential = template.auto_penalty_amount
            elif state == DeadlineState.OVERDUE:
                editing_end = deadline_at + LATE_WINDOW
                if now < editing_end and PenaltySource.LATE_WINDOW_EXPIRED not in penalties:
                    potential = template.auto_penalty_amount

            can_edit = bool(
                report
                and report.status not in REPORT_MODERATION_STATUSES
                and report.can_user_edit(now)
            )
            if report is None and report_date in {today, today - dt.timedelta(days=1)}:
                can_edit = now < deadline_at + LATE_WINDOW

            item = DeadlineItem(
                template_id=template_id,
                report_id=report.pk if report else None,
                report_date=report_date,
                title=template.name or f"Шаблон #{template.pk}",
                deadline_at=deadline_at.astimezone(MSK),
                state=state,
                editing_ends_at=editing_end.astimezone(MSK) if editing_end else None,
                charged_penalty=charged,
                potential_penalty=potential,
                can_edit=can_edit,
                can_open=report is not None,
            )
            deadline_date = deadline_at.astimezone(MSK).date()
            include = {
                DeadlineFilter.TODAY: deadline_date == today,
                DeadlineFilter.TOMORROW: deadline_date == today + dt.timedelta(days=1),
                DeadlineFilter.WEEK: today <= deadline_date <= horizon_end,
                DeadlineFilter.OVERDUE: state == DeadlineState.OVERDUE,
                DeadlineFilter.ALL: state == DeadlineState.OVERDUE or deadline_date <= horizon_end,
            }[selected_filter]
            if include:
                items.append(item)

        def sort_key(item: DeadlineItem):
            if item.state == DeadlineState.OVERDUE:
                critical = item.editing_ends_at or item.deadline_at
                return (0, critical)
            return (1, item.deadline_at)

        return sorted(items, key=sort_key)

    @classmethod
    def get_item(cls, user, template_id: int, report_date: dt.date, *, now=None) -> DeadlineItem | None:
        # Reuse the same projection and authorization rules.  The ALL view has
        # a bounded future horizon but includes every active overdue item.
        for item in cls.list_for_user(user, DeadlineFilter.ALL, now=now):
            if item.template_id == template_id and item.report_date == report_date:
                return item
        return None
