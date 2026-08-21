"""Repair rows and penalties affected by the legacy 00:00 deadline bug."""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.control.deadlines import LATE_WINDOW, MSK, calculate_report_deadline
from apps.control.models import (
    REPORT_MODERATION_STATUSES,
    EmployeeReport,
    Penalty,
    PenaltySource,
    PenaltyStatus,
    PenaltyType,
    ReportStatus,
)
from apps.control.services import ControlBalanceService


class Command(BaseCommand):
    help = "Dry-run or apply the idempotent 00:00 report deadline remediation"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist changes (default is dry-run)")

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        now = timezone.now()
        report_changes: list[dict] = []
        penalty_changes: list[dict] = []
        affected_by_user: dict[int, list[int]] = defaultdict(list)

        reports = EmployeeReport.objects.select_related("template").filter(
            report_date__isnull=False,
            deadline_at__isnull=False,
        )
        if apply_changes:
            # PostgreSQL cannot apply FOR UPDATE to nullable select_related
            # joins (EmployeeReport.template is nullable). Lock only the
            # domain row being repaired; dry-run performs no row locks at all.
            reports = reports.select_for_update(of=("self",))
        for report in reports:
            local_deadline = report.deadline_at.astimezone(MSK)
            if local_deadline.date() != report.report_date or local_deadline.time().replace(tzinfo=None) != dt.time(0, 0):
                continue
            old_deadline = report.deadline_at
            new_deadline = calculate_report_deadline(report.report_date, dt.time(0, 0))
            updates = {"deadline_at": new_deadline}
            timely = bool(report.first_submission_at and report.first_submission_at <= new_deadline)
            if timely:
                # The old calculation classified every same-day submission as
                # late.  Under the corrected deadline it is an ordinary timely
                # submission, so no 24-hour late window exists.
                updates["is_late_submission"] = False
                updates["late_window_ends_at"] = None
                if report.status != ReportStatus.ACCEPTED:
                    updates["editing_locked_at"] = new_deadline

                # A rejection before the real deadline must not start its
                # one-hour correction period.  Restore the normal deadline and
                # let a future post-deadline rejection open a fresh window.
                if report.correction_started_at and report.correction_started_at < new_deadline:
                    updates["correction_started_at"] = None
                    updates["correction_deadline"] = None
                    if report.status == ReportStatus.OVERDUE:
                        updates["status"] = ReportStatus.REJECTED
            else:
                if report.editing_locked_at == old_deadline:
                    updates["editing_locked_at"] = new_deadline
                if report.late_window_ends_at == old_deadline + LATE_WINDOW:
                    updates["late_window_ends_at"] = new_deadline + LATE_WINDOW
                if report.editing_locked_at == old_deadline + LATE_WINDOW:
                    updates["editing_locked_at"] = new_deadline + LATE_WINDOW
            report_changes.append({
                "report_id": report.pk,
                "old_deadline": old_deadline.isoformat(),
                "new_deadline": new_deadline.isoformat(),
                "fields": sorted(updates),
            })
            if apply_changes:
                for field, value in updates.items():
                    setattr(report, field, value)
                report.save(update_fields=[*updates, "updated_at"])

        penalties = Penalty.objects.select_related("user", "template", "report").filter(
            type=PenaltyType.AUTO,
            template__deadline_time=dt.time(0, 0),
            report_date__isnull=False,
            status=PenaltyStatus.ACCEPTED,
        )
        if apply_changes:
            penalties = penalties.select_for_update(of=("self",))
        for penalty in penalties:
            deadline = calculate_report_deadline(penalty.report_date, dt.time(0, 0))
            report = penalty.report
            classification = None
            if penalty.source == PenaltySource.DEADLINE_MISSED:
                if now < deadline:
                    classification = "PREMATURE"
                elif report and report.first_submission_at and report.first_submission_at <= deadline:
                    classification = "INVALID"
            elif penalty.source == PenaltySource.LATE_WINDOW_EXPIRED:
                late_end = deadline + LATE_WINDOW
                if now < late_end:
                    classification = "PREMATURE"
                elif report and (
                    report.status == "accepted"
                    or (
                        report.status in REPORT_MODERATION_STATUSES
                        and report.last_submission_at
                        and report.last_submission_at <= late_end
                    )
                ):
                    classification = "INVALID"
            elif penalty.source == PenaltySource.CORRECTION_EXPIRED and report:
                if penalty.created_at < deadline:
                    classification = "PREMATURE"
                elif report.correction_deadline and penalty.created_at < report.correction_deadline:
                    classification = "PREMATURE"

            if classification is None:
                continue
            marker = f"[AUTO-REMEDIATION:{classification}]"
            penalty_changes.append({
                "penalty_id": penalty.pk,
                "user_id": penalty.user_id,
                "source": penalty.source,
                "classification": classification.lower(),
                "amount": str(penalty.amount),
            })
            if not apply_changes:
                continue
            penalty.status = PenaltyStatus.REJECTED
            penalty.comment = f"{marker} Отменён после исправления семантики дедлайна 00:00."
            penalty.resolved_at = now
            penalty.save(update_fields=["status", "comment", "resolved_at", "updated_at"])
            affected_by_user[penalty.user_id].append(penalty.pk)
            if report:
                updates = []
                if penalty.source == PenaltySource.DEADLINE_MISSED:
                    report.initial_penalty_created_at = None
                    report.is_late_submission = bool(report.first_submission_at and report.first_submission_at > deadline)
                    updates.extend(["initial_penalty_created_at", "is_late_submission"])
                else:
                    report.additional_penalty_created_at = None
                    updates.append("additional_penalty_created_at")
                report.save(update_fields=[*updates, "updated_at"])

        if apply_changes:
            from apps.control.tasks import notify_penalty_remediation_task
            from apps.users.models import User

            for user_id, penalty_ids in affected_by_user.items():
                user = User.objects.get(pk=user_id)
                available = ControlBalanceService.get_available_balance(user)
                transaction.on_commit(
                    lambda uid=user_id, pids=list(penalty_ids), amount=str(available):
                    notify_penalty_remediation_task.delay(uid, pids, amount)
                )

        summary = {
            "mode": "apply" if apply_changes else "dry-run",
            "reports": report_changes,
            "penalties": penalty_changes,
            "affected_users": len(affected_by_user) if apply_changes else len({p["user_id"] for p in penalty_changes}),
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
