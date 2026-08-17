"""
CRM forms — used by web views for data entry and admin operations.
"""
from __future__ import annotations

import datetime

from django import forms

from apps.crm.models import FinanceEntry, ApplicationEntry, WeeklyPlan, CRMRole


class FinanceEntryForm(forms.ModelForm):
    class Meta:
        model  = FinanceEntry
        fields = ["income", "expenses", "kb_screenshot", "pp_earnings", "privat_earnings", "kb_balance", "notes"]
        widgets = {
            "income":          forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "expenses":        forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "pp_earnings":     forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "privat_earnings": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "0.00"}),
            "kb_balance":      forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "notes":           forms.Textarea(attrs={"rows": 3, "placeholder": "Дополнительные примечания..."}),
        }
        labels = {
            "income":          "Сумма поступлений ($)",
            "expenses":        "Сумма расходов / выплат ($)",
            "kb_screenshot":   "Скрин с КБ (файл)",
            "pp_earnings":     "Заработок с ПП за день ($)",
            "privat_earnings": "Заработок с Привата за день ($)",
            "kb_balance":      "Баланс КБ ($)",
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


class AdSlotCalculatorForm(forms.Form):
    weekly_target = forms.DecimalField(
        label="Цель на неделю", min_value=0, max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
    )
    average_price = forms.DecimalField(
        label="Средняя цена рекламы", min_value=0, max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
    )
    paid_slots = forms.IntegerField(label="Платная реклама", min_value=0, max_value=7)
    vp_slots = forms.IntegerField(label="Взаимный пиар", min_value=0, max_value=7)
    repayment_slots = forms.IntegerField(label="Отбив", min_value=0, max_value=7)

    def clean(self):
        cleaned = super().clean()
        values = [cleaned.get(name) for name in ("paid_slots", "vp_slots", "repayment_slots")]
        if all(value is not None for value in values) and sum(values) != 7:
            raise forms.ValidationError("Распределите ровно 7 слотов между рекламой, ВП и отбивом.")
        return cleaned
