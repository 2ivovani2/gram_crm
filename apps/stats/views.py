"""
Web stats dashboard for admin use.
URL: /stats/  — protected by Django's staff_member_required.

Supports GET params for filtering:
  ?start=YYYY-MM-DD  — start date (default: 30 days ago)
  ?end=YYYY-MM-DD    — end date   (default: today)
  ?preset=today|week|last_week|month  — shortcut presets (override start/end)
"""
from __future__ import annotations
import datetime
import json
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.stats.models import DailyReport, MissedDay
from apps.stats.services import DailyReportService
from apps.users.models import User, UserRole, UserStatus


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def _parse_date(value: str, fallback: datetime.date) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        return fallback


@method_decorator(staff_member_required, name="dispatch")
class StatsDashboardView(View):

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()

        # ── Resolve date range ────────────────────────────────────────────────
        preset = request.GET.get("preset", "")
        if preset == "today":
            start_date = end_date = today
        elif preset == "week":
            start_date = today - datetime.timedelta(days=today.weekday())
            end_date = today
        elif preset == "last_week":
            last_sunday = today - datetime.timedelta(days=today.weekday() + 1)
            start_date = last_sunday - datetime.timedelta(days=6)
            end_date = last_sunday
        elif preset == "month":
            start_date = today.replace(day=1)
            end_date = today
        else:
            default_start = today - datetime.timedelta(days=29)
            start_date = _parse_date(request.GET.get("start", ""), default_start)
            end_date = _parse_date(request.GET.get("end", ""), today)
            if start_date > end_date:
                start_date = end_date
            if end_date > today:
                end_date = today

        # ── Load reports ──────────────────────────────────────────────────────
        reports = DailyReportService.get_reports_for_period(start_date, end_date)
        today_report = next((r for r in reports if r.date == today), None)
        week_reports = DailyReportService.get_week_reports()

        # ── Missed days ───────────────────────────────────────────────────────
        missed_days = list(
            MissedDay.objects.filter(date__gte=start_date, date__lte=end_date).order_by("-date")
        )
        missed_count = len(missed_days)
        missed_filled_count = sum(1 for m in missed_days if m.is_filled)

        # ── Financial summaries ───────────────────────────────────────────────
        financial_summary = DailyReportService.build_financial_summary(today_report, week_reports)
        period_financial_summary = DailyReportService.build_period_financial_summary(reports)

        top_worker = DailyReportService.get_top_worker_week()

        # ── User counts ───────────────────────────────────────────────────────
        total_workers = User.objects.filter(role=UserRole.WORKER).count()
        total_curators = User.objects.filter(role=UserRole.CURATOR).count()
        active_workers = User.objects.filter(
            role__in=[UserRole.WORKER, UserRole.CURATOR],
            status=UserStatus.ACTIVE,
        ).count()

        # ── Chart data ────────────────────────────────────────────────────────
        days_in_range = (end_date - start_date).days + 1
        dates_range = [start_date + datetime.timedelta(days=i) for i in range(days_in_range)]
        by_date = {r.date: r for r in reports}
        missed_dates_set = {m.date for m in missed_days}

        chart_labels = [d.strftime("%d.%m") for d in dates_range]
        chart_applications = [
            by_date[d].total_applications if d in by_date else 0 for d in dates_range
        ]
        chart_worker_payout = [
            float(by_date[d].total_worker_payout) if d in by_date else 0 for d in dates_range
        ]
        chart_our_profit = [
            float(by_date[d].total_our_profit) if d in by_date else 0 for d in dates_range
        ]
        # 1 = missed and unfilled, for chart annotation
        chart_missed = [
            1 if d in missed_dates_set and d not in by_date else 0 for d in dates_range
        ]

        top_workers = list(
            User.objects.filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                attracted_count__gt=0,
            ).order_by("-attracted_count")[:10]
        )

        recent_reports = list(
            DailyReport.objects.filter(date__gte=start_date, date__lte=end_date).order_by("-date")[:30]
        )

        context = {
            "today": today,
            "start_date": start_date,
            "end_date": end_date,
            "preset": preset,
            "today_report": today_report,
            "week_reports": week_reports,
            "financial_summary": financial_summary,
            "period_financial_summary": period_financial_summary,
            "top_worker": top_worker,
            "total_workers": total_workers,
            "total_curators": total_curators,
            "active_workers": active_workers,
            "top_workers": top_workers,
            "recent_reports": recent_reports,
            "missed_days": missed_days,
            "missed_count": missed_count,
            "missed_filled_count": missed_filled_count,
            "chart_labels_json": json.dumps(chart_labels),
            "chart_applications_json": json.dumps(chart_applications),
            "chart_worker_payout_json": json.dumps(chart_worker_payout),
            "chart_our_profit_json": json.dumps(chart_our_profit),
            "chart_missed_json": json.dumps(chart_missed),
        }
        return render(request, "stats_dashboard.html", context)
