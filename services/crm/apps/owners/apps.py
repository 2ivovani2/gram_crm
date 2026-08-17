from django.apps import AppConfig


class OwnersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.owners"
    verbose_name = "Центр владельцев"

    def ready(self):
        from . import signals  # noqa: F401
