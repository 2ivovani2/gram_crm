from datetime import date

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.crm.models import CRMRole, Workspace, WorkspaceMembership
from apps.owners.crypto import decrypt_secret
from apps.owners.models import (
    OwnerAuditLog, OwnerChannel, OwnerChannelHistory, OwnerStatus, SavedOwnerFilter,
    TechnicalState, TelegramOwner,
)
from apps.owners.services import create_owner, recalculate
from apps.users.models import User, UserRole, UserStatus


pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return User.objects.create_user(
        telegram_id=991001,
        username="owner-admin",
        telegram_username="owner_admin",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        password="test-password",
    )


@pytest.fixture
def workspace(admin_user):
    workspace = Workspace.objects.create(name="Gramly", slug="gramly", created_by=admin_user)
    WorkspaceMembership.objects.create(
        workspace=workspace, user=admin_user, role=CRMRole.OWNER, is_active=True
    )
    return workspace


@pytest.fixture
def owner_status(workspace):
    return OwnerStatus.objects.get(workspace=workspace, name="Рабочий")


def owner_payload(owner_status):
    return {
        "phone": "+79990001122",
        "telegram_id": "55667788",
        "telegram_username": "@channel_owner",
        "display_name": "Александр",
        "registered_at": "2021-01-01",
        "used_since": date.today().isoformat(),
        "responsible": "",
        "status": str(owner_status.pk),
        "notes": "Рабочая запись",
        "sim_state": TechnicalState.READY,
        "session_state": TechnicalState.MISSING,
        "twofa_state": TechnicalState.READY,
        "proxy_state": TechnicalState.READY,
        "has_premium": "on",
        "session_secret": "session-sensitive-value",
    }


def test_owner_dashboard_requires_owner_role(client, workspace):
    worker = User.objects.create_user(
        telegram_id=991002, username="worker", role=UserRole.WORKER, status=UserStatus.ACTIVE
    )
    WorkspaceMembership.objects.create(
        workspace=workspace, user=worker, role=CRMRole.VIEWER, is_active=True
    )
    client.force_login(worker)
    response = client.get(reverse("owners:dashboard"))
    assert response.status_code == 403


def test_create_owner_encrypts_secrets_and_audits(client, admin_user, workspace, owner_status):
    client.force_login(admin_user)
    response = client.post(reverse("owners:create"), owner_payload(owner_status))
    assert response.status_code == 302
    owner = TelegramOwner.objects.get(workspace=workspace)
    assert owner.telegram_username == "channel_owner"
    assert owner.session_ciphertext != "session-sensitive-value"
    assert decrypt_secret(owner.session_ciphertext) == "session-sensitive-value"
    assert owner.session_state == TechnicalState.READY
    assert owner.health > 0
    assert owner.activity.filter(event_type="created").exists()
    assert owner.activity.filter(event_type="health_changed").exists()


def test_workspace_identity_constraints_prevent_duplicates(workspace):
    TelegramOwner.objects.create(workspace=workspace, phone="+7001", telegram_id=101)
    with pytest.raises(IntegrityError), transaction.atomic():
        TelegramOwner.objects.create(workspace=workspace, phone="+7002", telegram_id=101)
    with pytest.raises(IntegrityError), transaction.atomic():
        TelegramOwner.objects.create(workspace=workspace, phone="+7001", telegram_id=102)


def test_health_and_rank_fall_for_critical_state(workspace):
    owner = TelegramOwner.objects.create(
        workspace=workspace,
        phone="+7003",
        registered_at=date(2020, 1, 1),
        sim_state=TechnicalState.READY,
        session_state=TechnicalState.READY,
        twofa_state=TechnicalState.READY,
        proxy_state=TechnicalState.READY,
    )
    recalculate(owner)
    owner.refresh_from_db()
    healthy = owner.health
    owner.is_blocked = True
    owner.session_state = TechnicalState.CRITICAL
    owner.save()
    recalculate(owner)
    owner.refresh_from_db()
    assert owner.health < healthy
    assert owner.rank in {"C", "D"}
    assert "Аккаунт заблокирован" in owner.attention_reasons


def test_edit_records_safe_audit_without_secret_value(client, admin_user, workspace, owner_status):
    owner = TelegramOwner.objects.create(workspace=workspace, phone="+7004", status=owner_status)
    payload = owner_payload(owner_status)
    payload.update({"phone": "+7004", "notes": "Новое примечание", "proxy_secret": "proxy-password"})
    client.force_login(admin_user)
    response = client.post(reverse("owners:edit", args=(owner.pk,)), payload)
    assert response.status_code == 302
    event = owner.activity.filter(event_type="updated").first()
    assert event is not None
    assert "proxy-password" not in str(event.metadata)
    assert "proxy_secret" in event.metadata["secrets_updated"]
    assert owner.activity.filter(event_type="notes_changed").exists()


def test_delete_preserves_audit_and_blocks_active_channels(client, admin_user, workspace):
    owner = TelegramOwner.objects.create(workspace=workspace, phone="+7005")
    log = OwnerAuditLog.objects.create(
        owner=owner, actor=admin_user, actor_username="admin", actor_role="Администратор",
        event_type="created", description="Создан",
    )
    channel = OwnerChannel.objects.create(workspace=workspace, owner=owner, title="News")
    client.force_login(admin_user)
    blocked = client.post(reverse("owners:action", args=(owner.pk, "delete")))
    owner.refresh_from_db()
    assert blocked.status_code == 302
    assert owner.deleted_at is None
    channel.is_archived = True
    channel.save()
    client.post(reverse("owners:action", args=(owner.pk, "delete")))
    owner.refresh_from_db()
    assert owner.deleted_at is not None
    assert OwnerAuditLog.objects.filter(pk=log.pk).exists()
    assert client.get(reverse("owners:detail", args=(owner.pk,))).status_code == 404


def test_cross_workspace_owner_is_not_visible(client, admin_user, workspace):
    other = Workspace.objects.create(name="Other", slug="other", created_by=admin_user)
    foreign = TelegramOwner.objects.create(workspace=other, phone="+7006")
    client.force_login(admin_user)
    assert client.get(reverse("owners:detail", args=(foreign.pk,))).status_code == 404


def test_audit_entry_cannot_be_changed_or_deleted(workspace):
    owner = TelegramOwner.objects.create(workspace=workspace, phone="+7007")
    event = OwnerAuditLog.objects.create(owner=owner, event_type="created", description="Создан")
    event.description = "Подменено"
    with pytest.raises(ValueError):
        event.save()
    with pytest.raises(ValueError):
        event.delete()


def test_channel_transfer_updates_both_owners_and_history(client, admin_user, workspace):
    source = TelegramOwner.objects.create(workspace=workspace, phone="+7010")
    target = TelegramOwner.objects.create(workspace=workspace, phone="+7011")
    channel = OwnerChannel.objects.create(workspace=workspace, owner=source, title="Transfer me")
    client.force_login(admin_user)
    response = client.post(
        reverse("owners:channel_action", args=(source.pk, channel.pk, "transfer")),
        {"target_owner": target.pk, "reason": "Балансировка"},
    )
    assert response.status_code == 302
    channel.refresh_from_db()
    assert channel.owner == target
    history = OwnerChannelHistory.objects.get(channel=channel)
    assert history.previous_owner == source
    assert history.new_owner == target
    assert source.activity.filter(event_type="channel_transferred").exists()
    assert target.activity.filter(event_type="channel_received").exists()


def test_status_delete_requires_replacement_and_reassigns(client, admin_user, workspace):
    old = OwnerStatus.objects.create(workspace=workspace, name="Old")
    new = OwnerStatus.objects.create(workspace=workspace, name="New")
    owner = TelegramOwner.objects.create(workspace=workspace, phone="+7012", status=old)
    client.force_login(admin_user)
    client.post(reverse("owners:status_action", args=(old.pk, "delete")))
    assert OwnerStatus.objects.filter(pk=old.pk).exists()
    client.post(
        reverse("owners:status_action", args=(old.pk, "delete")),
        {"replacement_status": new.pk},
    )
    owner.refresh_from_db()
    assert owner.status == new
    assert not OwnerStatus.objects.filter(pk=old.pk).exists()
    assert owner.activity.filter(event_type="status_changed").exists()


def test_saved_filter_is_scoped_to_user_and_workspace(client, admin_user, workspace):
    client.force_login(admin_user)
    response = client.post(
        reverse("owners:filter_action", args=("save",)),
        {"name": "Проблемные", "state": "attention", "rank": "D", "unsafe": "ignored"},
    )
    assert response.status_code == 302
    saved = SavedOwnerFilter.objects.get(user=admin_user, workspace=workspace)
    assert saved.query == {"state": "attention", "rank": "D"}


def test_new_workspace_receives_default_system_statuses(admin_user):
    workspace = Workspace.objects.create(name="New tenant", slug="new-tenant", created_by=admin_user)
    statuses = OwnerStatus.objects.filter(workspace=workspace, is_system=True)
    assert statuses.count() == 4
    assert statuses.filter(name="Рабочий").exists()
