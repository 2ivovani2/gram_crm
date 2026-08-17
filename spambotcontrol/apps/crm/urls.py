"""
CRM URL configuration.
All routes are under the /crm/ prefix (defined in config/urls.py).
"""
from django.urls import path
from django.views.generic import RedirectView

from apps.crm import views

app_name = "crm"

urlpatterns = [
    # ── Auth ─────────────────────────────────────────────────────────────────
    path("login/",           views.LoginView.as_view(),                name="login"),
    path("logout/",          views.LogoutView.as_view(),               name="logout"),

    # ── Root redirect ─────────────────────────────────────────────────────────
    path("", RedirectView.as_view(pattern_name="crm:dashboard"), name="index"),

    # ── Dashboard ─────────────────────────────────────────────────────────────
    path("dashboard/",        views.DashboardView.as_view(),       name="dashboard"),
    path("switch-workspace/", views.WorkspaceSwitchView.as_view(), name="switch_workspace"),

    # ── Cash Flow entry ───────────────────────────────────────────────────────
    path("entry/finance/",    views.FinanceEntryView.as_view(),    name="entry_finance"),
    path("calculator/",       views.AdSlotCalculatorView.as_view(), name="ad_calculator"),

    # ── History & reports ─────────────────────────────────────────────────────
    path("history/",                views.HistoryView.as_view(),       name="history"),
    path("history/<str:date_str>/", views.DayDetailView.as_view(),     name="day_detail"),
    path("reports/<int:pk>/",       views.ReportDetailView.as_view(),  name="report_detail"),
]
