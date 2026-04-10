from __future__ import annotations
import logging
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import Broadcast, BroadcastStatus, BroadcastAudience, BroadcastDeliveryLog, DeliveryStatus
from apps.users.models import User, UserStatus

logger = logging.getLogger(__name__)


class BroadcastService:

    @staticmethod
    def create(
        title: str,
        text: str,
        audience: str,
        created_by: User,
        parse_mode: str = "HTML",
    ) -> Broadcast:
        return Broadcast.objects.create(
            title=title,
            text=text,
            audience=audience,
            parse_mode=parse_mode,
            created_by=created_by,
        )

    @staticmethod
    def confirm(broadcast: Broadcast) -> Broadcast:
        if broadcast.status != BroadcastStatus.DRAFT:
            raise ValueError(f"Cannot confirm broadcast in status '{broadcast.status}'")
        broadcast.status = BroadcastStatus.CONFIRMED
        broadcast.save(update_fields=["status", "updated_at"])
        return broadcast

    @staticmethod
    def get_recipients_queryset(broadcast: Broadcast):
        base = User.objects.filter(is_blocked_bot=False)
        if broadcast.audience == BroadcastAudience.ALL:
            return base
        elif broadcast.audience == BroadcastAudience.ACTIVE:
            return base.filter(status=UserStatus.ACTIVE)
        elif broadcast.audience == BroadcastAudience.INVITED:
            return base.filter(is_activated=True)
        return base

    @staticmethod
    def launch(broadcast: Broadcast) -> str:
        """Start broadcast delivery via Celery. Returns task ID."""
        from apps.broadcasts.tasks import send_broadcast_task

        if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.CONFIRMED):
            raise ValueError(f"Cannot launch broadcast in status '{broadcast.status}'")

        recipients_qs = BroadcastService.get_recipients_queryset(broadcast)
        total = recipients_qs.count()

        with transaction.atomic():
            Broadcast.objects.filter(pk=broadcast.pk).update(
                status=BroadcastStatus.RUNNING,
                total_recipients=total,
                started_at=timezone.now(),
            )

        task = send_broadcast_task.delay(broadcast.pk)
        Broadcast.objects.filter(pk=broadcast.pk).update(celery_task_id=task.id)

        logger.info("Broadcast %d launched: task=%s recipients=%d", broadcast.pk, task.id, total)
        return task.id

    @staticmethod
    def log_delivery(broadcast_id: int, user: User, status: str, error: str = "") -> None:
        # Use update_or_create to safely handle duplicate entries (unique_together constraint)
        # without raising IntegrityError and corrupting the PostgreSQL transaction
        BroadcastDeliveryLog.objects.update_or_create(
            broadcast_id=broadcast_id,
            user=user,
            defaults={"status": status, "error_message": error},
        )
        if status == DeliveryStatus.SENT:
            Broadcast.objects.filter(pk=broadcast_id).update(sent_count=F("sent_count") + 1)
        else:
            Broadcast.objects.filter(pk=broadcast_id).update(failed_count=F("failed_count") + 1)

    @staticmethod
    def get_list(page: int = 1, page_size: int = 10) -> tuple[list[Broadcast], int]:
        from apps.common.utils import paginate
        qs = Broadcast.objects.order_by("-created_at")
        items, total, _ = paginate(qs, page, page_size)
        return items, total

    @staticmethod
    def get_delivery_logs(broadcast: Broadcast, page: int = 1, page_size: int = 15) -> tuple[list, int]:
        from apps.common.utils import paginate
        qs = BroadcastDeliveryLog.objects.filter(broadcast=broadcast).select_related("user").order_by("-sent_at")
        items, total, _ = paginate(qs, page, page_size)
        return items, total
