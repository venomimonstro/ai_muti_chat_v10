import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Создан локально"
        PENDING = "pending", "Ожидает оплаты"
        SUCCEEDED = "succeeded", "Оплачен"
        CANCELED = "canceled", "Отменён"

    class ReceiptStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Не требуется настройкой"
        PENDING = "pending", "Ожидается"
        SUCCEEDED = "succeeded", "Сформирован"
        FAILED = "failed", "Ошибка"
        LEGAL_REVIEW = "legal_review", "Нужна юридическая проверка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments"
    )
    provider = models.CharField(max_length=32, default="yookassa")
    provider_payment_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    amount_rub = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="RUB")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    confirmation_url = models.URLField(blank=True)
    return_url = models.URLField()
    description = models.CharField(max_length=128)
    receipt_status = models.CharField(
        max_length=20, choices=ReceiptStatus.choices, default=ReceiptStatus.LEGAL_REVIEW
    )
    provider_payload = models.JSONField(default=dict, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    credited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    payload_hash = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    result = models.CharField(max_length=80, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Refund(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Создан локально"
        PENDING = "pending", "В обработке"
        SUCCEEDED = "succeeded", "Завершён"
        CANCELED = "canceled", "Отменён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    provider_refund_id = models.CharField(max_length=80, unique=True, null=True, blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    amount_rub = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    provider_payload = models.JSONField(default=dict, blank=True)
    wallet_debited_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PaymentFeeVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, default="yookassa")
    payment_method = models.CharField(max_length=50, default="unknown")
    percent = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    fixed_rub = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    effective_from = models.DateTimeField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and PaymentFeeVersion.objects.filter(pk=self.pk).exists():
            raise ValidationError("Payment fee versions immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Payment fee versions cannot be deleted")


class PaymentCostSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="cost")
    fee_version = models.ForeignKey(
        PaymentFeeVersion, on_delete=models.PROTECT, null=True, blank=True
    )
    gross_rub = models.DecimalField(max_digits=14, decimal_places=2)
    net_received_rub = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    acquiring_fee_rub = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    source = models.CharField(max_length=32, default="provider")
    created_at = models.DateTimeField(auto_now_add=True)


class ReconciliationRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Выполняется"
        SUCCEEDED = "succeeded", "Завершено"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    checked_count = models.PositiveIntegerField(default=0)
    corrected_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
