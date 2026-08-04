from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from aiogram.types import Message, User
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import (
    Channel,
    Contact,
    EventLog,
    GreetingDelivery,
    JoinRequest,
    ManagedBot,
    Owner,
    WelcomeMedia,
    WelcomeDraft,
    WelcomeDraftMedia,
    WelcomeMessage,
    WelcomeMessageVersion,
)

logger = logging.getLogger(__name__)


def owner_from_telegram(user: User) -> Owner:
    owner, _ = Owner.objects.update_or_create(
        telegram_id=user.id,
        defaults={
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
        },
    )
    return owner


def owned_bot(owner_id: int, bot_id: int, *, active_only: bool = True) -> ManagedBot:
    qs = ManagedBot.objects.filter(pk=bot_id, owner_id=owner_id)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.get()


def create_managed_bot(owner: Owner, token: str, telegram_user: User) -> ManagedBot:
    with transaction.atomic():
        managed = ManagedBot(
            owner=owner,
            telegram_id=telegram_user.id,
            username=telegram_user.username or "",
            display_name=telegram_user.full_name,
        )
        managed.set_token(token)
        managed.save(force_insert=True)
        EventLog.objects.create(
            bot=managed,
            owner=owner,
            event_type="bot_registered",
            message=f"Подключён бот @{managed.username or managed.telegram_id}",
        )
    return managed


def contact_from_user(bot: ManagedBot, user: User, *, bot_started: bool = False) -> Contact:
    defaults = {
        "username": user.username or "",
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "language_code": user.language_code or "unknown",
    }
    contact, created = Contact.objects.get_or_create(bot=bot, telegram_id=user.id, defaults=defaults)
    updates = []
    if not created:
        for key, value in defaults.items():
            if getattr(contact, key) != value:
                setattr(contact, key, value)
                updates.append(key)
    if bot_started and not contact.bot_started:
        contact.bot_started = True
        updates.append("bot_started")
    if updates:
        updates.append("last_seen_at")
        contact.save(update_fields=updates)
    return contact


@transaction.atomic
def save_welcome_message(
    bot: ManagedBot,
    author_telegram_id: int,
    payload: dict,
    media_data: dict | None,
) -> WelcomeMessageVersion:
    message, _ = WelcomeMessage.objects.select_for_update().get_or_create(bot=bot)
    next_version = (message.versions.aggregate(value=Max("version"))["value"] or 0) + 1
    version = WelcomeMessageVersion.objects.create(
        message=message,
        version=next_version,
        author_telegram_id=author_telegram_id,
        payload=payload,
    )
    if media_data:
        WelcomeMedia.objects.create(version=version, position=0, **media_data)
    message.active_version = version
    message.save(update_fields=("active_version", "updated_at"))
    EventLog.objects.create(
        bot=bot,
        owner=bot.owner,
        event_type="welcome_changed",
        message=f"Приветствие обновлено, версия {next_version}",
    )
    return version


@transaction.atomic
def append_album_item(
    bot: ManagedBot,
    owner: Owner,
    media_group_id: str,
    telegram_message_id: int,
    item_payload: dict,
    media_data: dict,
) -> WelcomeDraft:
    draft, _ = WelcomeDraft.objects.select_for_update().get_or_create(
        bot=bot,
        media_group_id=media_group_id,
        defaults={"owner": owner, "payload": {"type": "media_group", "items": []}},
    )
    _, created = WelcomeDraftMedia.objects.get_or_create(
        draft=draft,
        telegram_message_id=telegram_message_id,
        defaults=media_data,
    )
    if created:
        payload = draft.payload
        items = payload.setdefault("items", [])
        item_payload["telegram_message_id"] = telegram_message_id
        items.append(item_payload)
        items.sort(key=lambda item: item["telegram_message_id"])
        draft.payload = payload
        draft.save(update_fields=("payload", "updated_at"))
    return draft


@transaction.atomic
def finalize_album(draft_id: int) -> WelcomeMessageVersion | None:
    draft = WelcomeDraft.objects.select_for_update().select_related("bot", "owner").filter(pk=draft_id).first()
    if not draft:
        return None
    message, _ = WelcomeMessage.objects.select_for_update().get_or_create(bot=draft.bot)
    next_version = (message.versions.aggregate(value=Max("version"))["value"] or 0) + 1
    version = WelcomeMessageVersion.objects.create(
        message=message,
        version=next_version,
        author_telegram_id=draft.owner.telegram_id,
        payload=draft.payload,
    )
    WelcomeMedia.objects.bulk_create([
        WelcomeMedia(
            version=version,
            position=position,
            media_type=item.media_type,
            storage_key=item.storage_key,
            original_name=item.original_name,
            mime_type=item.mime_type,
            size=item.size,
        )
        for position, item in enumerate(draft.media.all())
    ])
    message.active_version = version
    message.save(update_fields=("active_version", "updated_at"))
    EventLog.objects.create(
        bot=draft.bot,
        owner=draft.owner,
        event_type="welcome_changed",
        message=f"Приветствие-альбом обновлено, версия {next_version}",
    )
    draft.delete()
    return version


def statistics(bot: ManagedBot) -> dict:
    aggregate = bot.contacts.aggregate(
        total=Count("id"),
        live=Count("id", filter=Q(delivery_status=Contact.DeliveryStatus.LIVE)),
        dead=Count("id", filter=Q(delivery_status=Contact.DeliveryStatus.DEAD)),
        unknown=Count("id", filter=Q(delivery_status=Contact.DeliveryStatus.UNKNOWN)),
        male=Count("id", filter=Q(gender=Contact.Gender.MALE)),
        female=Count("id", filter=Q(gender=Contact.Gender.FEMALE)),
        transformer=Count("id", filter=Q(gender=Contact.Gender.UNKNOWN)),
    )
    aggregate["languages"] = list(
        bot.contacts.values("language_code").annotate(total=Count("id")).order_by("-total", "language_code")[:20]
    )
    return aggregate


@transaction.atomic
def schedule_greeting(bot: ManagedBot, channel: Channel, contact: Contact, event_key: str):
    try:
        version = bot.welcome_message.active_version
    except WelcomeMessage.DoesNotExist:
        return None
    if not version:
        return None
    # approveChatJoinRequest is normally followed by a chat_member update. Both
    # events may race, so lock the contact and coalesce the same join into one
    # greeting. A genuine later rejoin remains eligible after this small window.
    Contact.objects.select_for_update().get(pk=contact.pk)
    recent = (
        GreetingDelivery.objects.filter(
            bot=bot,
            channel=channel,
            contact=contact,
            created_at__gte=timezone.now() - timedelta(minutes=5),
        )
        .exclude(status=GreetingDelivery.Status.CANCELLED)
        .first()
    )
    if recent:
        return recent
    delay = bot.welcome_delay_seconds
    delivery, created = GreetingDelivery.objects.get_or_create(
        bot=bot,
        event_key=event_key,
        defaults={
            "channel": channel,
            "contact": contact,
            "version": version,
            "delay_snapshot_seconds": delay,
            "due_at": timezone.now() + timedelta(seconds=delay),
        },
    )
    if created:
        transaction.on_commit(lambda: _enqueue_delivery(delivery.id, delay))
    return delivery


def _enqueue_delivery(delivery_id: int, delay: int) -> None:
    from .tasks import send_greeting_task

    send_greeting_task.apply_async(args=(delivery_id,), countdown=delay)


@transaction.atomic
def create_join_request(bot: ManagedBot, channel: Channel, contact: Contact, update_id: int):
    delay = bot.approval_delay_seconds
    try:
        request = JoinRequest.objects.create(
            bot=bot,
            channel=channel,
            contact=contact,
            telegram_update_id=update_id,
            status=JoinRequest.Status.SCHEDULED if bot.auto_approve else JoinRequest.Status.PENDING,
            delay_snapshot_seconds=delay,
            due_at=timezone.now() + timedelta(seconds=delay) if bot.auto_approve else None,
        )
    except IntegrityError:
        return None
    if bot.auto_approve:
        transaction.on_commit(lambda: _enqueue_approval(request.id, delay))
    return request


def _enqueue_approval(request_id: int, delay: int) -> None:
    from .tasks import approve_join_request_task

    approve_join_request_task.apply_async(args=(request_id,), countdown=delay)


@transaction.atomic
def enable_auto_approve(bot: ManagedBot) -> int:
    bot = ManagedBot.objects.select_for_update().get(pk=bot.pk)
    bot.auto_approve = True
    bot.save(update_fields=("auto_approve", "updated_at"))
    pending_ids = list(
        bot.join_requests.filter(status=JoinRequest.Status.PENDING).values_list("id", flat=True)
    )
    bot.join_requests.filter(id__in=pending_ids).update(
        status=JoinRequest.Status.SCHEDULED,
        delay_snapshot_seconds=0,
        due_at=timezone.now(),
    )
    transaction.on_commit(lambda: [_enqueue_approval(pk, 0) for pk in pending_ids])
    return len(pending_ids)


@transaction.atomic
def disable_auto_approve(bot: ManagedBot) -> int:
    bot = ManagedBot.objects.select_for_update().get(pk=bot.pk)
    bot.auto_approve = False
    bot.save(update_fields=("auto_approve", "updated_at"))
    # Countdown tasks cannot be reliably revoked after worker restart. Moving
    # rows back to PENDING makes those tasks harmless and keeps the requests for
    # the next activation.
    return bot.join_requests.filter(status=JoinRequest.Status.SCHEDULED).update(
        status=JoinRequest.Status.PENDING,
        due_at=None,
    )


@transaction.atomic
def delete_managed_bot(owner: Owner, bot_id: int) -> ManagedBot:
    bot = ManagedBot.objects.select_for_update().get(pk=bot_id, owner=owner, is_active=True)
    bot.is_active = False
    bot.save(update_fields=("is_active", "updated_at"))
    # Cascading delete is deliberately done after webhook removal by the caller.
    return bot


def fingerprint_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]
