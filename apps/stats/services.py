"""Stats service: weekly chart, financial summary, top worker, daily report creation."""
from __future__ import annotations
import datetime
from decimal import Decimal
from typing import Optional

from django.utils import timezone


class DailyReportService:

    @staticmethod
    def get_or_create_for_today() -> tuple:
        """Return (report, created) for today in Moscow timezone."""
        from .models import DailyReport
        today = timezone.localdate()
        return DailyReport.objects.get_or_create(date=today)

    @staticmethod
    def exists_for_today() -> bool:
        from .models import DailyReport
        return DailyReport.objects.filter(date=timezone.localdate()).exists()

    @staticmethod
    def exists_for_date(date: datetime.date) -> bool:
        from .models import DailyReport
        return DailyReport.objects.filter(date=date).exists()

    @staticmethod
    def create_report(
        date: datetime.date,
        link: str,
        client_nick: str,
        client_rate: Decimal,
        total_applications: int,
        created_by,
    ):
        from .models import DailyReport, MissedDay, RateConfig
        config = RateConfig.get()
        computed = config.compute(client_rate)
        report, _ = DailyReport.objects.update_or_create(
            date=date,
            defaults=dict(
                link=link,
                client_nick=client_nick,
                client_rate=client_rate,
                total_applications=total_applications,
                worker_rate=computed["worker_rate"],
                referral_rate=computed["referral_rate"],
                our_profit=computed["our_profit"],
                broadcast_sent=False,
                created_by=created_by,
            ),
        )
        # If this date was previously marked as missed, mark it as filled now
        MissedDay.objects.filter(date=date, filled_at__isnull=True).update(
            filled_at=timezone.now(),
            filled_by=created_by,
        )
        return report

    @staticmethod
    def get_reports_for_period(start_date: datetime.date, end_date: datetime.date) -> list:
        """Return DailyReport objects for the given date range, ordered by date."""
        from .models import DailyReport
        return list(
            DailyReport.objects.filter(date__gte=start_date, date__lte=end_date).order_by("date")
        )

    @staticmethod
    def get_week_reports() -> list:
        """Return DailyReport objects for Mon–today of the current week (Moscow timezone)."""
        today = timezone.localdate()
        monday = today - datetime.timedelta(days=today.weekday())
        return DailyReportService.get_reports_for_period(monday, today)

    @staticmethod
    def get_last_week_reports() -> list:
        """Return DailyReport objects for Mon–Sun of the previous week."""
        today = timezone.localdate()
        last_sunday = today - datetime.timedelta(days=today.weekday() + 1)
        last_monday = last_sunday - datetime.timedelta(days=6)
        return DailyReportService.get_reports_for_period(last_monday, last_sunday)

    @staticmethod
    def get_month_reports() -> list:
        """Return DailyReport objects for the current calendar month (Moscow timezone)."""
        today = timezone.localdate()
        month_start = today.replace(day=1)
        return DailyReportService.get_reports_for_period(month_start, today)

    @staticmethod
    def get_date_range_for_period(period: str) -> tuple:
        """Return (start_date, end_date) for the given period name."""
        today = timezone.localdate()
        if period == "today":
            return today, today
        if period == "week":
            return today - datetime.timedelta(days=today.weekday()), today
        if period == "last_week":
            last_sunday = today - datetime.timedelta(days=today.weekday() + 1)
            last_monday = last_sunday - datetime.timedelta(days=6)
            return last_monday, last_sunday
        if period == "month":
            return today.replace(day=1), today
        # fallback: last 30 days
        return today - datetime.timedelta(days=29), today

    @staticmethod
    def get_unfilled_recent_dates(days: int = 7) -> list:
        """
        Return dates in the last `days` days (excluding today) that have no DailyReport.
        Sorted newest first.
        """
        from .models import DailyReport
        today = timezone.localdate()
        candidates = [today - datetime.timedelta(days=i) for i in range(1, days + 1)]
        existing = set(
            DailyReport.objects.filter(date__in=candidates).values_list("date", flat=True)
        )
        return [d for d in candidates if d not in existing]

    @staticmethod
    def get_missed_dates_set(dates: list) -> set:
        """Return a set of dates from the given list that have an unfilled MissedDay record."""
        from .models import MissedDay
        return set(
            MissedDay.objects.filter(date__in=dates, filled_at__isnull=True)
            .values_list("date", flat=True)
        )

    @staticmethod
    def count_missed_days(start_date: datetime.date, end_date: datetime.date) -> int:
        from .models import MissedDay
        return MissedDay.objects.filter(date__gte=start_date, date__lte=end_date).count()

    @staticmethod
    def get_top_worker_week() -> Optional[tuple]:
        """Return (user, attracted_count) for top worker this week, or None."""
        from apps.users.models import User, UserRole, UserStatus
        user = (
            User.objects.filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                status=UserStatus.ACTIVE,
            )
            .order_by("-attracted_count")
            .first()
        )
        if user and user.attracted_count > 0:
            return user, user.attracted_count
        return None

    @staticmethod
    def build_weekly_bar_chart(reports: list, week_start: datetime.date = None) -> str:
        """
        Build ASCII bar chart Mon–Sun for the week starting at week_start.
        If week_start is None, uses current week's Monday.
        Future days are shown as empty.
        """
        RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        today = timezone.localdate()
        if week_start is None:
            week_start = today - datetime.timedelta(days=today.weekday())

        by_date: dict[datetime.date, int] = {r.date: r.total_applications for r in reports}

        max_val = max(by_date.values(), default=1) or 1
        BAR_WIDTH = 8
        lines = []
        for i in range(7):
            day = week_start + datetime.timedelta(days=i)
            label = RU_DAYS[i]
            if day > today:
                lines.append(f"{label}  ░░░░░░░░ —")
                continue
            val = by_date.get(day, 0)
            filled = round(val / max_val * BAR_WIDTH)
            bar = "█" * filled + "░" * (BAR_WIDTH - filled)
            lines.append(f"{label}  {bar} {val}")
        return "\n".join(lines)

    @staticmethod
    def build_financial_summary(today_report, week_reports: list) -> str:
        def _fmt(v: Decimal) -> str:
            return f"{v:.2f} ₽"

        if not today_report:
            day_income = day_worker = day_referral = Decimal("0")
        else:
            day_income = today_report.total_our_profit
            day_worker = today_report.total_worker_payout
            day_referral = today_report.total_referral_payout

        week_income = sum((r.total_our_profit for r in week_reports), Decimal("0"))
        week_worker = sum((r.total_worker_payout for r in week_reports), Decimal("0"))
        week_referral = sum((r.total_referral_payout for r in week_reports), Decimal("0"))

        return (
            "💼 <b>Финансы</b>\n"
            f"  Доход за день:          <b>{_fmt(day_income)}</b>\n"
            f"  Доход за неделю:       <b>{_fmt(week_income)}</b>\n"
            f"  Долг спамерам за день: <b>{_fmt(day_worker)}</b>\n"
            f"  Долг спамерам за нед.: <b>{_fmt(week_worker)}</b>\n"
            f"  Долг рефам за день:    <b>{_fmt(day_referral)}</b>\n"
            f"  Долг рефам за нед.:    <b>{_fmt(week_referral)}</b>"
        )

    @staticmethod
    def build_period_financial_summary(reports: list) -> str:
        """Financial summary for an arbitrary list of reports (any period)."""
        def _fmt(v: Decimal) -> str:
            return f"{v:.2f} ₽"

        total_income = sum((r.total_our_profit for r in reports), Decimal("0"))
        total_worker = sum((r.total_worker_payout for r in reports), Decimal("0"))
        total_referral = sum((r.total_referral_payout for r in reports), Decimal("0"))
        total_apps = sum(r.total_applications for r in reports)

        return (
            "💼 <b>Финансы за период</b>\n"
            f"  Заявок всего:          <b>{total_apps}</b>\n"
            f"  Наш доход:             <b>{_fmt(total_income)}</b>\n"
            f"  Долг спамерам:         <b>{_fmt(total_worker)}</b>\n"
            f"  Долг рефералам:        <b>{_fmt(total_referral)}</b>"
        )
