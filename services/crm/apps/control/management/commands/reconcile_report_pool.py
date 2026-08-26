from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.control.models import EmployeeReport, REPORT_MODERATION_STATUSES
from apps.control.tasks import queue_report_submission_notification
from apps.users.models import User, UserRole, UserStatus


class Command(BaseCommand):
    help = "Audit reports awaiting moderation and safely enqueue missing admin notifications."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Only print findings (default).")
        mode.add_argument("--apply", action="store_true", help="Queue missing notifications.")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        active_admins = User.objects.filter(
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            is_blocked_bot=False,
        )
        pending = EmployeeReport.objects.filter(
            status__in=REPORT_MODERATION_STATUSES,
        ).select_related("user", "template", "template__created_by")

        malformed = pending.filter(
            Q(report_date__isnull=True)
            | Q(first_submission_at__isnull=True)
            | Q(user__isnull=True)
        )
        unroutable = pending.filter(
            template__created_by__isnull=False,
        ).exclude(
            template__created_by__status=UserStatus.ACTIVE,
            template__created_by__is_blocked_bot=False,
        )
        completed_without_delivery = pending.filter(
            submission_completed_at__isnull=False,
            submission_deliveries__isnull=True,
        ).distinct()

        self.stdout.write(
            f"mode={'apply' if apply else 'dry-run'} pending={pending.count()} "
            f"active_admins={active_admins.count()} malformed={malformed.count()} "
            f"unroutable={unroutable.count()} missing_delivery={completed_without_delivery.count()}"
        )
        for report in malformed.order_by("id"):
            self.stdout.write(f"malformed report_id={report.pk} user_id={report.user_id}")
        for report in unroutable.order_by("id"):
            self.stdout.write(
                f"unroutable report_id={report.pk} template_id={report.template_id} "
                f"creator_id={report.template.created_by_id if report.template else None}"
            )
        if apply:
            queued = 0
            for report_id in completed_without_delivery.values_list("id", flat=True):
                queued += int(queue_report_submission_notification(report_id))
            self.stdout.write(self.style.SUCCESS(f"queued={queued}"))
