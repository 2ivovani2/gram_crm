"""Explicit, CSRF-protected two-step access editor; no JavaScript required."""
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.crm.access import (
    PERMISSIONS, ROLE_PERMISSIONS, StaleAccess, effective_permissions,
    normalize_overrides, update_access,
)
from apps.crm.models import WorkspaceMembership
from apps.crm.views import CRMPermissionMixin
from apps.users.models import User, UserRole

SALT = "crm-access-preview-v1"
DESCRIPTIONS = {
    "admin": "Все операционные разделы и управление доступом других сотрудников.",
    "curator": "Модерация отчётов сотрудников и бухгалтеров, шаблоны, предложение штрафов. Без подтверждения штрафов и изменения баланса.",
    "accountant": "Cash Flow, обработка выводов, бухгалтерская аналитика.",
    "worker": "Собственные отчёты, дедлайны, баланс и личные операции в боте.",
    "anonymous": "Нет доступа к рабочим разделам.",
}


class AccessForm(forms.Form):
    role = forms.ChoiceField(label="Базовая роль", choices=UserRole.choices)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        role = self.data.get("role") if self.is_bound else self.initial.get("role", "anonymous")
        defaults = ROLE_PERMISSIONS.get(role, frozenset())
        for p in PERMISSIONS:
            if p.code == "access.manage":
                continue
            self.fields[p.code] = forms.ChoiceField(
                label=p.label, choices=[("role", "По роли — разрешено" if p.code in defaults else "По роли — запрещено"), ("allow", "Разрешить вручную"), ("deny", "Запретить вручную")],
                initial="role",
            )


class UserAccessView(CRMPermissionMixin, View):
    required_permission = "access.manage"

    def _target(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user.pk == request.crm_user.pk:
            raise PermissionDenied("Нельзя изменять собственный доступ")
        return user

    def _initial(self, request, target):
        membership = WorkspaceMembership.objects.filter(workspace=request.crm_workspace, user=target).first()
        initial = {"role": target.role, "version": membership.access_version if membership else 0}
        for p in PERMISSIONS:
            value = (membership.permission_overrides if membership else {}).get(p.code)
            initial[p.code] = "allow" if value is True else "deny" if value is False else "role"
        return initial, membership

    def _render(self, request, target, form, *, preview=None, status=200):
        initial, membership = self._initial(request, target)
        groups = {}
        for p in PERMISSIONS:
            if p.code != "access.manage":
                groups.setdefault(p.group, []).append(form[p.code])
        current = effective_permissions(target.role, membership.permission_overrides if membership else {})
        return render(request, "control/user_access.html", {
            **self.get_crm_context(request), "target": target, "form": form,
            "groups": groups.items(), "preview": preview, "membership_record": membership,
            "role_description": DESCRIPTIONS[target.role],
            "current_actions": [p.label for p in PERMISSIONS if p.code in current],
        }, status=status)

    def get(self, request, pk):
        target = self._target(request, pk)
        initial, _ = self._initial(request, target)
        return self._render(request, target, AccessForm(initial=initial))

    def post(self, request, pk):
        target = self._target(request, pk)
        initial, membership = self._initial(request, target)
        if request.POST.get("action") == "save":
            try:
                payload = signing.loads(request.POST.get("confirmation", ""), salt=SALT, max_age=900)
                if (payload["actor"], payload["target"], payload["workspace"]) != (request.crm_user.pk, target.pk, request.crm_workspace.pk):
                    raise PermissionDenied("Подтверждение относится к другой операции")
                update_access(request.crm_user, target, payload["role"], payload["overrides"], payload["version"],
                              workspace=request.crm_workspace, confirm_role_change=payload["role_change"])
            except signing.BadSignature:
                form = AccessForm(initial)
                form.is_valid()
                form.add_error(None, "Подтверждение истекло или повреждено. Проверьте права заново.")
                return self._render(request, target, form, status=400)
            except ValidationError as exc:
                form = AccessForm(initial)
                form.is_valid()
                form.add_error(None, exc)
                return self._render(request, target, form, status=409 if isinstance(exc, StaleAccess) else 400)
            messages.success(request, "Права CRM сохранены. Они применяются к следующим действиям в вебе и боте.")
            return redirect("control:users")
        form = AccessForm(request.POST)
        if not form.is_valid():
            return self._render(request, target, form, status=400)
        if form.cleaned_data["version"] != initial["version"]:
            form.add_error(None, "Права уже изменены. Откройте форму заново.")
            return self._render(request, target, form, status=409)
        role = form.cleaned_data["role"]
        role_change = role != target.role
        reset = request.POST.get("action") == "reset" or role_change
        overrides = {} if reset else {p.code: form.cleaned_data[p.code] == "allow" for p in PERMISSIONS
                                     if p.code != "access.manage" and form.cleaned_data[p.code] != "role"}
        overrides = normalize_overrides(role, overrides)
        before = effective_permissions(target.role, membership.permission_overrides if membership else {})
        after = effective_permissions(role, overrides)
        payload = {"actor": request.crm_user.pk, "target": target.pk, "workspace": request.crm_workspace.pk,
                   "role": role, "overrides": overrides, "version": initial["version"], "role_change": role_change}
        preview = {
            "confirmation": signing.dumps(payload, salt=SALT),
            "role": dict(UserRole.choices)[role], "description": DESCRIPTIONS[role], "reset": reset,
            "added": [p.label for p in PERMISSIONS if p.code in after - before],
            "removed": [p.label for p in PERMISSIONS if p.code in before - after],
            "actions": [p.label for p in PERMISSIONS if p.code in after],
        }
        return self._render(request, target, form, preview=preview)
