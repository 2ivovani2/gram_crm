from celery import current_app
from django.conf import settings


def test_django_process_uses_project_celery_app():
    assert current_app.main == "gramly_crm"
    assert current_app.conf.broker_url == settings.CELERY_BROKER_URL
    assert current_app.conf.broker_url.startswith("redis://")
