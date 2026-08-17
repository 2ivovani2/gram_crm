from celery import shared_task

from .models import TelegramOwner
from .services import recalculate


@shared_task(name="apps.owners.tasks.recalculate_owner_health_task")
def recalculate_owner_health_task():
    processed = 0
    changed = 0
    for owner in TelegramOwner.objects.filter(deleted_at__isnull=True).iterator(chunk_size=500):
        old = (owner.health, owner.rank)
        recalculate(owner, reason="Плановый мониторинг")
        processed += 1
        changed += old != (owner.health, owner.rank)
    return {"processed": processed, "changed": changed}
