from django.urls import path
from apps.control import views

app_name = "control"

urlpatterns = [
    path("", views.ControlDashboardView.as_view(), name="dashboard"),

    # Report templates (admin CRUD)
    path("report-templates/", views.ReportTemplatesView.as_view(), name="report_templates"),
    path("report-templates/<int:pk>/", views.ReportTemplateEditView.as_view(), name="report_template_edit"),

    # Reports
    path("reports/", views.ReportsListView.as_view(), name="reports"),
    path("reports/<int:pk>/", views.ReportDetailView.as_view(), name="report_detail"),

    # Penalties
    path("penalties/", views.PenaltiesListView.as_view(), name="penalties"),
    path("penalties/<int:pk>/action/", views.PenaltyActionView.as_view(), name="penalty_action"),

    # Employees / KPI
    path("employees/", views.EmployeesView.as_view(), name="employees"),
    path("employees/<int:user_id>/kpi/", views.EmployeeKPIView.as_view(), name="employee_kpi"),

    # Withdrawals
    path("withdrawals/", views.WithdrawalsListView.as_view(), name="withdrawals"),
    path("withdrawals/<int:pk>/action/", views.WithdrawalActionView.as_view(), name="withdrawal_action"),

    # Analytics
    path("analytics/", views.AnalyticsView.as_view(), name="analytics"),

    # User management
    path("users/", views.UsersListView.as_view(), name="users"),
    path("users/<int:pk>/edit/", views.UserEditView.as_view(), name="user_edit"),
]
