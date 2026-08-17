from django import forms
from django.core.exceptions import ValidationError

from apps.users.models import User, UserStatus

from .models import OwnerChannel, OwnerStatus, TechnicalState, TelegramOwner


class OwnerForm(forms.ModelForm):
    session_secret = forms.CharField(required=False, label="Новая Session", widget=forms.PasswordInput(render_value=False))
    twofa_secret = forms.CharField(required=False, label="Новый пароль 2FA", widget=forms.PasswordInput(render_value=False))
    proxy_secret = forms.CharField(required=False, label="Новый Proxy", widget=forms.PasswordInput(render_value=False))
    sim_secret = forms.CharField(required=False, label="Новые данные SIM", widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = TelegramOwner
        fields = (
            "phone", "telegram_id", "telegram_username", "display_name", "registered_at", "used_since",
            "responsible", "status", "notes", "sim_state", "session_state", "twofa_state", "proxy_state",
            "has_premium", "is_scam", "is_blocked",
        )
        widgets = {
            "registered_at": forms.DateInput(attrs={"type": "date"}),
            "used_since": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace
        self.fields["status"].queryset = OwnerStatus.objects.filter(workspace=workspace)
        self.fields["responsible"].queryset = User.objects.filter(
            status=UserStatus.ACTIVE, is_active=True
        ).exclude(role="anonymous").order_by("first_name", "telegram_username")
        self.fields["phone"].required = not bool(self.instance and self.instance.telegram_id)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "g-input")

    def clean_phone(self):
        value = self.cleaned_data.get("phone", "").strip().replace(" ", "")
        if value and not value.lstrip("+").isdigit():
            raise ValidationError("Номер может содержать только + и цифры.")
        return value

    def clean_telegram_username(self):
        return self.cleaned_data.get("telegram_username", "").strip().lstrip("@")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("phone") and not cleaned.get("telegram_id"):
            raise ValidationError("Укажите номер телефона или Telegram ID.")
        if self.workspace:
            candidates = TelegramOwner.objects.filter(workspace=self.workspace)
            if self.instance and self.instance.pk:
                candidates = candidates.exclude(pk=self.instance.pk)
            phone = cleaned.get("phone")
            telegram_id = cleaned.get("telegram_id")
            if phone and candidates.filter(phone=phone).exists():
                self.add_error("phone", "Владелец с этим номером уже существует.")
            if telegram_id and candidates.filter(telegram_id=telegram_id).exists():
                self.add_error("telegram_id", "Владелец с этим Telegram ID уже существует.")
        return cleaned

    @property
    def secret_values(self):
        return {name: self.cleaned_data.get(name, "") for name in (
            "session_secret", "twofa_secret", "proxy_secret", "sim_secret"
        )}


class StatusForm(forms.ModelForm):
    class Meta:
        model = OwnerStatus
        fields = ("name", "color", "emoji", "description")
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        queryset = OwnerStatus.objects.filter(workspace=self.workspace, name__iexact=name)
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if self.workspace and queryset.exists():
            raise ValidationError("Статус с таким названием уже существует.")
        return name


class ChannelForm(forms.ModelForm):
    class Meta:
        model = OwnerChannel
        fields = ("title", "username", "telegram_id", "status")

    def clean_username(self):
        return self.cleaned_data.get("username", "").strip().lstrip("@")

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace

    def clean_telegram_id(self):
        telegram_id = self.cleaned_data.get("telegram_id")
        if telegram_id and OwnerChannel.objects.filter(
            workspace=self.workspace, telegram_id=telegram_id
        ).exists():
            raise ValidationError("Канал с этим Telegram ID уже существует.")
        return telegram_id
