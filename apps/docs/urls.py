from django.urls import path
from .views import DocsIndexView, DocsCRMView, DocsSpamControlView, DocsRatesView, DocsFAQView

app_name = "docs"

urlpatterns = [
    path("",             DocsIndexView.as_view(),       name="index"),
    path("crm/",         DocsCRMView.as_view(),          name="crm"),
    path("spamcontrol/", DocsSpamControlView.as_view(),  name="spamcontrol"),
    path("rates/",       DocsRatesView.as_view(),        name="rates"),
    path("faq/",         DocsFAQView.as_view(),          name="faq"),
]
