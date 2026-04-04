from django.db import models


class WithdrawalMethod(models.TextChoices):
    CRYPTOBOT = "cryptobot", "CryptoBot"
    USDT_TRC20 = "usdt_trc20", "USDT TRC20"


class WithdrawalStatus(models.TextChoices):
    PENDING = "pending", "Ожидает обработки"
    APPROVED = "approved", "Исполнена"
    REJECTED = "rejected", "Отклонена"


class WithdrawalRequest(models.Model):
    """A worker's request to withdraw their balance."""

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="withdrawals",
        verbose_name="Пользователь",
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        verbose_name="Сумма (руб.)",
    )
    method = models.CharField(
        max_length=20,
        choices=WithdrawalMethod.choices,
        verbose_name="Способ выплаты",
    )
    details = models.CharField(
        max_length=255,
        verbose_name="Реквизиты",
        help_text="@username для CryptoBot или адрес кошелька для USDT TRC20",
    )
    status = models.CharField(
        max_length=20,
        choices=WithdrawalStatus.choices,
        default=WithdrawalStatus.PENDING,
        db_index=True,
        verbose_name="Статус",
    )

    # Admin who processed the request
    processed_by = models.ForeignKey(
        "users.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="processed_withdrawals",
        verbose_name="Обработал",
    )
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата обработки")

    # Stores list of {"telegram_id": int, "message_id": int} for admin notifications
    admin_notifications = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Заявка на вывод"
        verbose_name_plural = "Заявки на вывод"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Вывод #{self.pk} {self.user} — {self.amount} руб. [{self.status}]"

    def get_method_display_short(self) -> str:
        return dict(WithdrawalMethod.choices).get(self.method, self.method)
