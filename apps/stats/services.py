"""Stats service: weekly chart, financial summary, top worker, daily report creation."""
from __future__ import annotations
import datetime
from decimal import Decimal
from typing import Optional


class DailyReportService:

    @staticmethod
    def get_or_create_for_today() -> tuple:
        """Return (report, created) for today."""
        from .models import DailyReport
        today = datetime.date.today()
        return DailyReport.objects.get_or_create(date=today)

    @staticmethod
    def exists_for_today() -> bool:
        from .models import DailyReport
        return DailyReport.objects.filter(date=datetime.date.today()).exists()

    @staticmethod
    def create_report(
        date: datetime.date,
        link: str,
        client_nick: str,
        client_rate: Decimal,
        total_applications: int,
        created_by,
    ):
        from .models import DailyReport, RateConfig
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
        return report

    @staticmethod
    def get_week_reports() -> list:
        """Return DailyReport objects for Mon–today of the current week."""
        from .models import DailyReport
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
        return list(
            DailyReport.objects.filter(date__gte=monday, date__lte=today).order_by("date")
        )

    @staticmethod
    def get_top_worker_week() -> Optional[tuple]:
        """Return (user, attracted_count) for top worker this week, or None."""
        from apps.users.models import User, UserRole, UserStatus
        # Top by attracted_count among active workers/curators
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
    def build_weekly_bar_chart(reports: list) -> str:
        """Build ASCII bar chart Mon–Sun. Missing days show as empty."""
        RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())

        # Map date → total_applications
        by_date: dict[datetime.date, int] = {r.date: r.total_applications for r in reports}

        max_val = max(by_date.values(), default=1) or 1
        BAR_WIDTH = 8
        lines = []
        for i in range(7):
            day = monday + datetime.timedelta(days=i)
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
