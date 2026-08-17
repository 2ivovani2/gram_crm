from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.crm.models import Workspace

from .models import OwnerStatus


DEFAULT_OWNER_STATUSES = (
    ("Рабочий", "#596623", "Аккаунт доступен для штатной работы."),
    ("Требует проверки", "#BD8330", "Нужна ручная проверка сотрудника."),
    ("Не использовать", "#A63E36", "Работа с аккаунтом временно запрещена."),
    ("Ожидает проверки", "#55CFE8", "Новая запись ожидает первичной проверки."),
)


@receiver(post_save, sender=Workspace, dispatch_uid="owners_seed_workspace_statuses")
def seed_workspace_owner_statuses(sender, instance, created, **kwargs):
    if not created:
        return
    OwnerStatus.objects.bulk_create([
        OwnerStatus(
            workspace=instance, name=name, color=color, description=description, is_system=True
        )
        for name, color, description in DEFAULT_OWNER_STATUSES
    ], ignore_conflicts=True)
