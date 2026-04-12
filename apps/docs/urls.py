from django.urls import path
from .views import (
    DocsIndexView, DocsCRMView, DocsSpamControlView, DocsRatesView, DocsFAQView,
    DocsLoginView, DocsAuthCallbackView, DocsLogoutView,
)

app_name = "docs"

urlpatterns = [
    # Auth
    path("login/",         DocsLoginView.as_view(),        name="login"),
    path("auth/callback/", DocsAuthCallbackView.as_view(), name="auth_callback"),
    path("logout/",        DocsLogoutView.as_view(),       name="logout"),
    # Content
    path("",             DocsIndexView.as_view(),       name="index"),
    path("crm/",         DocsCRMView.as_view(),          name="crm"),
    path("spamcontrol/", DocsSpamControlView.as_view(),  name="spamcontrol"),
    path("rates/",       DocsRatesView.as_view(),        name="rates"),
    path("faq/",         DocsFAQView.as_view(),          name="faq"),
]
