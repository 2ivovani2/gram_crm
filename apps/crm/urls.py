"""
CRM URL configuration.
All routes are under the /crm/ prefix (defined in config/urls.py).

Public:
  /crm/login/              — login page (Telegram widget)
  /crm/auth/callback/      — Telegram auth callback (GET params from widget)
  /crm/logout/             — POST to logout

Authenticated:
  /crm/                    — redirect to dashboard
  /crm/dashboard/          — main dashboard
  /crm/switch-workspace/   — POST to switch active workspace
  /crm/entry/finance/      — finance data entry form
  /crm/entry/apps/         — applications data entry form
  /crm/history/            — history / date-range view
  /crm/reports/<pk>/       — report detail
  /crm/export/             — Excel export (GET with optional ?start=&end= params)

Admin (OWNER only):
  /crm/admin/              — admin panel index
  /crm/admin/members/      — member management
  /crm/admin/plans/        — weekly plans
  /crm/admin/generate/<date>/ — POST to manually generate a report for a date
"""
from django.urls import path
from django.views.generic import RedirectView

from apps.crm import views

app_name = "crm"

urlpatterns = [
    # ── Auth ─────────────────────────────────────────────────────────────────
    path("login/",           views.LoginView.as_view(),                name="login"),
    path("auth/callback/",   views.TelegramAuthCallbackView.as_view(), name="auth_callback"),
    path("logout/",          views.LogoutView.as_view(),               name="logout"),

    # ── Root redirect ─────────────────────────────────────────────────────────
    path("", RedirectView.as_view(pattern_name="crm:dashboard"), name="index"),

    # ── Dashboard ─────────────────────────────────────────────────────────────
    path("dashboard/",          views.DashboardView.as_view(),       name="dashboard"),
    path("switch-workspace/",   views.WorkspaceSwitchView.as_view(), name="switch_workspace"),

    # ── Entry forms ───────────────────────────────────────────────────────────
    path("entry/finance/",      views.FinanceEntryView.as_view(),      name="entry_finance"),
    path("entry/apps/",         views.ApplicationEntryView.as_view(),  name="entry_apps"),

    # ── History & reports ─────────────────────────────────────────────────────
    path("history/",            views.HistoryView.as_view(),          name="history"),
    path("reports/<int:pk>/",   views.ReportDetailView.as_view(),     name="report_detail"),

    # ── Export ────────────────────────────────────────────────────────────────
    path("export/",             views.ExportView.as_view(),           name="export"),

    # ── Admin (OWNER only) ────────────────────────────────────────────────────
    path("admin/",              views.AdminIndexView.as_view(),        name="admin"),
    path("admin/members/",      views.AdminMembersView.as_view(),      name="admin_members"),
    path("admin/plans/",        views.AdminPlansView.as_view(),        name="admin_plans"),
    path("admin/generate/<str:date_str>/", views.GenerateReportView.as_view(), name="generate_report"),
]
