"""
Web stats dashboard for admin use.
URL: /stats/  — protected by Django's login_required (superuser only).
"""
from __future__ import annotations
import datetime
import json
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.stats.models import DailyReport
from apps.stats.services import DailyReportService
from apps.users.models import User, UserRole, UserStatus


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


@method_decorator(staff_member_required, name="dispatch")
class StatsDashboardView(View):

    def get(self, request, *args, **kwargs):
        today = timezone.localdate()
        monday = today - datetime.timedelta(days=today.weekday())

        # Last 30 days of daily reports
        reports_30 = list(
            DailyReport.objects.filter(
                date__gte=today - datetime.timedelta(days=29),
                date__lte=today,
            ).order_by("date")
        )

        # Week reports for bar chart
        week_reports = DailyReportService.get_week_reports()

        # Today's report
        today_report = next((r for r in week_reports if r.date == today), None)

        # Financial summary
        financial_summary = DailyReportService.build_financial_summary(today_report, week_reports)

        # Top worker
        top_worker = DailyReportService.get_top_worker_week()

        # User counts
        total_workers = User.objects.filter(role=UserRole.WORKER).count()
        total_curators = User.objects.filter(role=UserRole.CURATOR).count()
        active_workers = User.objects.filter(
            role__in=[UserRole.WORKER, UserRole.CURATOR],
            status=UserStatus.ACTIVE,
        ).count()

        # Chart data: last 30 days applications
        dates_30 = [(today - datetime.timedelta(days=29 - i)) for i in range(30)]
        by_date = {r.date: r for r in reports_30}
        chart_labels = [d.strftime("%d.%m") for d in dates_30]
        chart_applications = [by_date.get(d, None) and by_date[d].total_applications or 0 for d in dates_30]
        chart_worker_payout = [
            float(by_date[d].total_worker_payout) if d in by_date else 0 for d in dates_30
        ]
        chart_our_profit = [
            float(by_date[d].total_our_profit) if d in by_date else 0 for d in dates_30
        ]

        # Top workers by attracted_count
        top_workers = list(
            User.objects.filter(
                role__in=[UserRole.WORKER, UserRole.CURATOR],
                attracted_count__gt=0,
            ).order_by("-attracted_count")[:10]
        )

        # Recent reports (last 14 for table)
        recent_reports = list(DailyReport.objects.order_by("-date")[:14])

        context = {
            "today": today,
            "today_report": today_report,
            "week_reports": week_reports,
            "financial_summary": financial_summary,
            "top_worker": top_worker,
            "total_workers": total_workers,
            "total_curators": total_curators,
            "active_workers": active_workers,
            "top_workers": top_workers,
            "recent_reports": recent_reports,
            # JSON for Chart.js
            "chart_labels_json": json.dumps(chart_labels),
            "chart_applications_json": json.dumps(chart_applications),
            "chart_worker_payout_json": json.dumps(chart_worker_payout),
            "chart_our_profit_json": json.dumps(chart_our_profit),
        }
        return render(request, "stats_dashboard.html", context)
