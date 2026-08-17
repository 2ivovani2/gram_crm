from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from .crypto import encrypt_secret
from .models import OwnerAuditLog, OwnerRank, TechnicalState, TelegramOwner


def actor_snapshot(actor):
    if actor is None:
        return "system", "Система"
    username = actor.telegram_username or actor.display_name or str(actor.telegram_id)
    return username.lstrip("@"), actor.get_role_display()


def log_event(owner, actor, event_type, description, metadata=None):
    username, role = actor_snapshot(actor)
    return OwnerAuditLog.objects.create(
        owner=owner,
        actor=actor,
        actor_username=username,
        actor_role=role,
        event_type=event_type,
        description=description,
        metadata=metadata or {},
    )


def calculate_health(owner: TelegramOwner, channel_count: int | None = None) -> int:
    score = 100
    weights = {
        TechnicalState.READY: 0,
        TechnicalState.ATTENTION: 8,
        TechnicalState.CRITICAL: 20,
        TechnicalState.MISSING: 15,
    }
    score -= weights[owner.session_state] + weights[owner.sim_state]
    score -= max(0, weights[owner.twofa_state] - 3)
    score -= max(0, weights[owner.proxy_state] - 5)
    score += 4 if owner.has_premium else 0
    score -= 35 if owner.is_scam else 0
    score -= 45 if owner.is_blocked else 0
    count = owner.channel_count if channel_count is None else channel_count
    score -= min(25, max(0, count - 7) * 4)
    return max(0, min(100, score))


def calculate_rank(owner: TelegramOwner, health: int, channel_count: int | None = None) -> str:
    score = health
    today = date.today()
    if owner.registered_at:
        age_days = (today - owner.registered_at).days
        score += 8 if age_days >= 1095 else 5 if age_days >= 365 else 0
    if owner.used_since:
        work_days = (today - owner.used_since).days
        score += 6 if work_days >= 365 else 3 if work_days >= 90 else 0
    count = owner.channel_count if channel_count is None else channel_count
    score -= max(0, count - 10) * 2
    if score >= 96:
        return OwnerRank.S
    if score >= 82:
        return OwnerRank.A
    if score >= 68:
        return OwnerRank.B
    if score >= 50:
        return OwnerRank.C
    return OwnerRank.D


def recalculate(owner: TelegramOwner, actor=None, reason="Изменение параметров"):
    old_health, old_rank = owner.health, owner.rank
    channel_count = owner.channels.filter(is_archived=False).count()
    health = calculate_health(owner, channel_count)
    rank = calculate_rank(owner, health, channel_count)
    changed = []
    if health != old_health:
        owner.health = health
        changed.append("health")
    if rank != old_rank:
        owner.rank = rank
        changed.append("rank")
    if changed:
        owner.save(update_fields=[*changed, "updated_at"])
        if old_health != health:
            log_event(
                owner, actor, "health_changed", f"Индекс здоровья: {old_health}% → {health}%",
                {"old": old_health, "new": health, "reason": reason},
            )
        if old_rank != rank:
            log_event(
                owner, actor, "rank_changed", f"Ранг: {old_rank} → {rank}",
                {"old": old_rank, "new": rank, "reason": reason},
            )
    return owner


@transaction.atomic
def create_owner(*, workspace, actor, cleaned_data, secrets):
    owner = TelegramOwner(**cleaned_data, workspace=workspace)
    apply_secrets(owner, secrets)
    owner.full_clean()
    owner.save()
    log_event(owner, actor, "created", "Создан владелец")
    return recalculate(owner, actor, "Первоначальный расчёт")


@transaction.atomic
def update_owner(*, owner, actor, cleaned_data, secrets):
    changed = []
    old_status = owner.status
    old_notes = owner.notes
    for field, value in cleaned_data.items():
        if getattr(owner, field) != value:
            setattr(owner, field, value)
            changed.append(field)
    secret_changed = apply_secrets(owner, secrets)
    if old_notes != owner.notes:
        owner.notes_updated_by = actor
        owner.notes_updated_at = timezone.now()
        changed.extend(["notes_updated_by", "notes_updated_at"])
    owner.full_clean()
    if changed or secret_changed:
        owner.save()
        safe_changed = [field for field in changed if field not in {"notes"}]
        log_event(
            owner, actor, "updated", "Обновлены данные владельца",
            {"fields": safe_changed, "secrets_updated": secret_changed},
        )
    if old_status_id(old_status) != old_status_id(owner.status):
        log_event(
            owner, actor, "status_changed",
            f"Статус: {old_status or 'Без статуса'} → {owner.status or 'Без статуса'}",
        )
    if old_notes != owner.notes:
        log_event(owner, actor, "notes_changed", "Обновлено примечание")
    return recalculate(owner, actor)


def old_status_id(status):
    return status.pk if status else None


def apply_secrets(owner, secrets):
    changed = []
    mapping = {
        "session_secret": ("session_ciphertext", "session_state"),
        "twofa_secret": ("twofa_ciphertext", "twofa_state"),
        "proxy_secret": ("proxy_ciphertext", "proxy_state"),
        "sim_secret": ("sim_ciphertext", "sim_state"),
    }
    for input_name, (storage_name, state_name) in mapping.items():
        value = (secrets.get(input_name) or "").strip()
        if not value:
            continue
        setattr(owner, storage_name, encrypt_secret(value))
        if getattr(owner, state_name) == TechnicalState.MISSING:
            setattr(owner, state_name, TechnicalState.READY)
        changed.append(input_name)
    return changed
