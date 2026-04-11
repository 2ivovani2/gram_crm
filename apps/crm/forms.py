"""
CRM forms — used by web views for data entry and admin operations.
"""
from __future__ import annotations

import datetime

from django import forms
from django.utils import timezone

from apps.crm.models import FinanceEntry, ApplicationEntry, WeeklyPlan, WorkspaceMembership, CRMRole


class FinanceEntryForm(forms.ModelForm):
    class Meta:
        model  = FinanceEntry
        fields = ["income", "expenses", "kb_screenshot", "pp_earnings", "privat_earnings", "notes"]
        widgets = {
            "income":          forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "expenses":        forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "pp_earnings":     forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "privat_earnings": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "notes":           forms.Textarea(attrs={"rows": 3, "placeholder": "Дополнительные примечания..."}),
        }
        labels = {
            "income":          "Сумма поступлений ($)",
            "expenses":        "Сумма расходов / выплат ($)",
            "kb_screenshot":   "Скрин с КБ (файл)",
            "pp_earnings":     "Заработок с ПП за день ($)",
            "privat_earnings": "Заработок с Привата за день ($)",
            "notes":           "Примечания",
        }


class ApplicationEntryForm(forms.ModelForm):
    class Meta:
        model  = ApplicationEntry
        fields = ["applications_count", "applications_earnings", "notes"]
        widgets = {
            "applications_count":    forms.NumberInput(attrs={"min": "0", "placeholder": "0"}),
            "applications_earnings": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "notes":                 forms.Textarea(attrs={"rows": 3, "placeholder": "Дополнительные примечания..."}),
        }
        labels = {
            "applications_count":    "Количество заявок за день",
            "applications_earnings": "Заработок с заявок за день ($)",
            "notes":                 "Примечания",
        }


class WeeklyPlanForm(forms.ModelForm):
    week_start = forms.DateField(
        label="Начало недели (Пн)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model  = WeeklyPlan
        fields = ["week_start", "pp_plan", "privat_plan"]
        widgets = {
            "pp_plan":     forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "privat_plan": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
        }
        labels = {
            "pp_plan":     "План ПП на неделю ($)",
            "privat_plan": "План Привата на неделю ($)",
        }

    def clean_week_start(self):
        date = self.cleaned_data["week_start"]
        if date.weekday() != 0:
            # Snap to Monday automatically
            date = date - datetime.timedelta(days=date.weekday())
        return date


class MemberRoleForm(forms.Form):
    user_id = forms.IntegerField(widget=forms.HiddenInput)
    role    = forms.ChoiceField(
        choices=CRMRole.choices,
        label="Роль",
        widget=forms.Select(),
    )


class AddMemberForm(forms.Form):
    telegram_id = forms.IntegerField(
        label="Telegram ID пользователя",
        help_text="Числовой ID (не username). Пользователь должен уже быть зарегистрирован в боте.",
        widget=forms.NumberInput(attrs={"placeholder": "123456789"}),
    )
    role = forms.ChoiceField(
        choices=CRMRole.choices,
        label="Роль в пространстве",
    )


class DateRangeForm(forms.Form):
    start = forms.DateField(
        label="Начало",
        widget=forms.DateInput(attrs={"type": "date"}),
        required=False,
    )
    end = forms.DateField(
        label="Конец",
        widget=forms.DateInput(attrs={"type": "date"}),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end   = cleaned.get("end")
        if start and end and start > end:
            raise forms.ValidationError("Дата начала должна быть раньше даты окончания.")
        return cleaned
