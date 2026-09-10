"""CRM-owned authorization. SSO authenticates an identity, never grants rights.

Never cache this snapshot in a session/FSM. Resolve it immediately before each
user operation; object ownership checks remain the responsibility of services.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.crm.models import AccessChange, CRMRole, Workspace, WorkspaceMembership
from apps.users.models import User, UserRole, UserStatus


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    group: str
    requires: str | None = None


PERMISSIONS = (
    Permission("dashboard.view", "HR-дашборд", "Отчёты"),
    Permission("personal.use", "Собственные отчёты, дедлайны и баланс", "Личные операции"),
    Permission("reports.view", "Просмотр отчётов", "Отчёты"),
    Permission("reports.moderate", "Модерация отчётов", "Отчёты", "reports.view"),
    Permission("templates.view", "Просмотр шаблонов", "Шаблоны"),
    Permission("templates.manage", "Изменение шаблонов", "Шаблоны", "templates.view"),
    Permission("templates.financial", "Суммы автоматических штрафов", "Шаблоны", "templates.manage"),
    Permission("penalties.view", "Просмотр штрафов", "Штрафы"),
    Permission("penalties.create", "Предложение штрафа на проверку", "Штрафы", "penalties.view"),
    Permission("penalties.resolve", "Подтверждение, отмена и удаление", "Штрафы", "penalties.view"),
    Permission("employees.view", "Просмотр сотрудников", "Сотрудники"),
    Permission("employees.manage", "Изменение сотрудников", "Сотрудники", "employees.view"),
    Permission("employees.finance", "Изменение ставки и баланса", "Сотрудники", "employees.view"),
    Permission("finance.view", "Просмотр Cash Flow", "Cash Flow"),
    Permission("finance.manage", "Изменение Cash Flow", "Cash Flow", "finance.view"),
    Permission("withdrawals.view", "Просмотр заявок", "Выводы"),
    Permission("withdrawals.manage", "Обработка выводов", "Выводы", "withdrawals.view"),
    Permission("analytics.view", "Просмотр аналитики", "Аналитика"),
    Permission("analytics.export", "Экспорт аналитики", "Аналитика", "analytics.view"),
    Permission("calculator.use", "Калькулятор", "Калькулятор"),
    Permission("owners.view", "Просмотр Центра владельцев", "Центр владельцев"),
    Permission("owners.manage", "Изменение Центра владельцев", "Центр владельцев", "owners.view"),
    Permission("notifications.view", "Просмотр уведомлений", "Уведомления"),
    Permission("notifications.manage", "Управление уведомлениями", "Уведомления", "notifications.view"),
    Permission("settings.manage", "Глобальные настройки", "Настройки"),
    Permission("access.manage", "Управление доступом (только администратор)", "Доступ"),
)
CATALOG = {p.code: p for p in PERMISSIONS}
ROLE_PERMISSIONS = {
    UserRole.ADMIN: frozenset(CATALOG),
    UserRole.CURATOR: frozenset({"personal.use", "dashboard.view", "reports.view", "reports.moderate", "templates.view", "templates.manage", "penalties.view", "penalties.create"}),
    UserRole.ACCOUNTANT: frozenset({"personal.use", "dashboard.view", "finance.view", "finance.manage", "withdrawals.view", "withdrawals.manage", "analytics.view", "calculator.use"}),
    UserRole.WORKER: frozenset({"personal.use", "calculator.use"}),
    UserRole.ANONYMOUS: frozenset(),
}
LEGACY_ROLE_MAP = {UserRole.ADMIN: CRMRole.OWNER, UserRole.ACCOUNTANT: CRMRole.FINANCE, UserRole.CURATOR: CRMRole.VIEWER, UserRole.WORKER: CRMRole.VIEWER}


def normalize_overrides(role, overrides):
    """Denying a prerequisite removes dependants; grants enable prerequisites.

Explicit parent denial wins a contradictory child grant. The form previews
this normalized result before saving, rather than silently granting access.
"""
    if role not in ROLE_PERMISSIONS or not isinstance(overrides, dict):
        raise ValidationError("Некорректная роль или настройки доступа")
    for code, value in overrides.items():
        if code not in CATALOG or code == "access.manage" or type(value) is not bool:
            raise ValidationError("Недопустимое индивидуальное разрешение")
    normalized = dict(overrides)
    for _ in PERMISSIONS:
        previous = normalized.copy()
        for p in PERMISSIONS:
            if p.requires and normalized.get(p.requires) is False:
                normalized[p.code] = False
            elif p.requires and normalized.get(p.code) is True:
                normalized[p.requires] = True
        if normalized == previous:
            break
    return normalized


def effective_permissions(role, overrides):
    normalized = normalize_overrides(role, overrides)
    result = set(ROLE_PERMISSIONS[role])
    for code, allowed in normalized.items():
        if allowed:
            result.add(code)
        else:
            result.discard(code)
    for _ in PERMISSIONS:
        for p in PERMISSIONS:
            if p.requires and p.requires not in result:
                result.discard(p.code)
    if role != UserRole.ADMIN:
        result.discard("access.manage")
    return frozenset(result)


@dataclass(frozen=True)
class Access:
    role: str = "anonymous"
    allowed: frozenset[str] = frozenset()
    reason: str = "denied"
    membership_id: int | None = None
    version: int = 0

    def allows(self, code):
        return code in self.allowed


def get_access(user, workspace=None):
    if not getattr(user, "pk", None):
        return Access(reason="unauthenticated")
    # Request.user and Telegram middleware data may predate a revocation.
    fresh = User.objects.filter(pk=user.pk).only("role", "status", "is_active").first()
    if not fresh or not fresh.is_active or fresh.status != UserStatus.ACTIVE:
        return Access(reason="inactive")
    if fresh.role == UserRole.ANONYMOUS:
        return Access(reason="anonymous")
    memberships = WorkspaceMembership.objects.select_related("workspace").filter(user_id=fresh.pk)
    if workspace is None:
        memberships = memberships.filter(workspace__slug="gramly")
    else:
        memberships = memberships.filter(workspace_id=getattr(workspace, "pk", workspace))
    membership = memberships.first()
    if not membership:
        return Access(role=fresh.role, reason="membership_missing")
    if not membership.is_active or not membership.workspace.is_active:
        return Access(role=fresh.role, reason="revoked", membership_id=membership.pk, version=membership.access_version)
    role = fresh.role
    if membership.workspace.slug != "gramly":
        # Outside the migrated workspace the compatibility field still gates
        # legacy features. It cannot confer access-management privileges.
        legacy = {CRMRole.OWNER: UserRole.ADMIN, CRMRole.FINANCE: UserRole.ACCOUNTANT, CRMRole.VIEWER: UserRole.WORKER}
        role = legacy.get(membership.role, UserRole.WORKER)
    try:
        allowed = effective_permissions(role, membership.permission_overrides)
    except ValidationError:
        return Access(role=fresh.role, reason="invalid_configuration")
    if fresh.role != UserRole.ADMIN:
        allowed = allowed - {"access.manage"}
    return Access(fresh.role, frozenset(allowed), "allowed", membership.pk, membership.access_version)


def has_permission(user, code, workspace=None):
    return get_access(user, workspace).allows(code)


def permission_context(user, workspace=None):
    access = get_access(user, workspace)
    return {p.code.replace(".", "_"): access.allows(p.code) for p in PERMISSIONS}


class StaleAccess(ValidationError):
    pass


def _locked_context(actor, target, workspace):
    query = Workspace.objects.select_for_update()
    ws = query.get(pk=getattr(workspace, "pk", workspace)) if workspace is not None else query.get(slug="gramly")
    if ws.slug != "gramly":
        raise ValidationError("Перенос прав доступен только для пространства gramly")
    if actor.pk == target.pk:
        raise PermissionDenied("Нельзя изменять собственный доступ")
    users = {u.pk: u for u in User.objects.select_for_update().filter(pk__in=[actor.pk, target.pk]).order_by("pk")}
    actor, target = users[actor.pk], users[target.pk]
    if not has_permission(actor, "access.manage", ws):
        raise PermissionDenied("Управление доступом доступно только администраторам CRM")
    return ws, actor, target


def _snapshot(user, membership):
    return {"role": user.role, "status": user.status, "active": user.is_active,
            "membership_active": membership.is_active if membership else None,
            "overrides": membership.permission_overrides if membership else {},
            "version": membership.access_version if membership else 0}


def _protect_last_admin(target, workspace):
    if target.role == UserRole.ADMIN and not WorkspaceMembership.objects.filter(
        workspace=workspace, is_active=True, user__role=UserRole.ADMIN,
        user__is_active=True, user__status=UserStatus.ACTIVE,
    ).exclude(user=target).exists():
        raise PermissionDenied("Нельзя отключить последнего действующего администратора")


@transaction.atomic
def update_access(actor, target, role, overrides, expected_version, workspace=None, confirm_role_change=False):
    ws, actor, target = _locked_context(actor, target, workspace)
    membership = WorkspaceMembership.objects.select_for_update().filter(user=target, workspace=ws).first()
    version = membership.access_version if membership else 0
    if type(expected_version) is not int or expected_version != version:
        raise StaleAccess("Права уже изменены. Обновите страницу и проверьте новые значения.")
    if role != target.role and not confirm_role_change:
        raise ValidationError("Подтвердите смену роли и сброс прежних индивидуальных настроек")
    if role != target.role and overrides:
        raise ValidationError("При смене роли прежние настройки сбрасываются. Индивидуальные права можно задать после сохранения роли.")
    overrides = normalize_overrides(role, overrides)
    if role != UserRole.ADMIN:
        _protect_last_admin(target, ws)
    before = _snapshot(target, membership)
    if membership is None:
        membership = WorkspaceMembership.objects.create(user=target, workspace=ws, role=LEGACY_ROLE_MAP.get(role, CRMRole.VIEWER), invited_by=actor, joined_at=timezone.now())
    if target.role == role and membership.permission_overrides == overrides and before["membership_active"] is not None:
        return membership
    target.role = role
    target.save(update_fields=["role", "updated_at"])
    membership.permission_overrides = overrides
    membership.access_version += 1
    membership.save(update_fields=["permission_overrides", "access_version", "updated_at"])
    AccessChange.objects.create(workspace=ws, actor=actor, target=target, action="access_updated", before=before, after=_snapshot(target, membership))
    return membership


@transaction.atomic
def update_user_status(actor, target, status, workspace=None):
    if status not in UserStatus.values:
        raise ValidationError("Неизвестный статус")
    ws, actor, target = _locked_context(actor, target, workspace)
    membership = WorkspaceMembership.objects.select_for_update().filter(user=target, workspace=ws).first()
    if target.status == status:
        return target
    if status != UserStatus.ACTIVE:
        _protect_last_admin(target, ws)
    before = _snapshot(target, membership)
    target.status = status
    if status == UserStatus.INACTIVE and not target.deactivated_at:
        target.deactivated_at = timezone.now()
    target.save(update_fields=["status", "deactivated_at", "updated_at"])
    if membership:
        membership.access_version += 1
        membership.save(update_fields=["access_version", "updated_at"])
    AccessChange.objects.create(workspace=ws, actor=actor, target=target, action="status_updated", before=before, after=_snapshot(target, membership))
    return target


@transaction.atomic
def revoke_access(actor, target, workspace=None, expected_version=None):
    ws, actor, target = _locked_context(actor, target, workspace)
    membership = WorkspaceMembership.objects.select_for_update().get(user=target, workspace=ws)
    if expected_version is not None and expected_version != membership.access_version:
        raise StaleAccess("Права уже изменены. Обновите страницу.")
    if not membership.is_active:
        return membership
    _protect_last_admin(target, ws)
    before = _snapshot(target, membership)
    membership.is_active = False
    membership.access_version += 1
    membership.save(update_fields=["is_active", "access_version", "updated_at"])
    AccessChange.objects.create(workspace=ws, actor=actor, target=target, action="access_revoked", before=before, after=_snapshot(target, membership))
    return membership
