import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet"
    )
    available_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reserved_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    paid_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promo_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    updated_at = models.DateTimeField(auto_now=True)


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        CREDIT = "credit", "Пополнение"
        RESERVE = "reserve", "Резерв"
        RELEASE = "release", "Освобождение"
        DEBIT = "debit", "Списание"
        REFUND = "refund", "Возврат пополнения"
        ADJUSTMENT = "adjustment", "Корректировка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount_rub = models.DecimalField(max_digits=14, decimal_places=4)
    available_delta_rub = models.DecimalField(max_digits=14, decimal_places=4)
    reserved_delta_rub = models.DecimalField(max_digits=14, decimal_places=4)
    paid_delta_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promo_delta_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    available_after_rub = models.DecimalField(max_digits=14, decimal_places=4)
    reserved_after_rub = models.DecimalField(max_digits=14, decimal_places=4)
    source_type = models.CharField(max_length=64)
    source_id = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and LedgerEntry.objects.filter(pk=self.pk).exists():
            raise ValidationError("Ledger entries immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted")


class BalanceReservation(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Активен"
        SETTLED = "settled", "Закрыт"
        RELEASED = "released", "Освобождён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="reservations")
    amount_rub = models.DecimalField(max_digits=14, decimal_places=4)
    paid_amount_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promo_amount_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    actual_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.ACTIVE)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True)


class PriceVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_slug = models.SlugField(db_index=True)
    input_rub_per_million = models.DecimalField(max_digits=14, decimal_places=4)
    output_rub_per_million = models.DecimalField(max_digits=14, decimal_places=4)
    provider_currency = models.CharField(max_length=3, default="RUB")
    input_price_per_million = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True
    )
    output_price_per_million = models.DecimalField(
        max_digits=14, decimal_places=6, null=True, blank=True
    )
    markup_percent = models.DecimalField(max_digits=7, decimal_places=2, default=100)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and PriceVersion.objects.filter(pk=self.pk).exists():
            raise ValidationError("Price versions immutable; create a new version")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Price versions cannot be deleted")


class RequestCost(models.Model):
    class ReconciliationStatus(models.TextChoices):
        PENDING = "pending", "Ожидает сверки"
        OK = "ok", "Сверено"
        UNDERCHARGED = "undercharged", "Недосписание"
        OVERCHARGED = "overcharged", "Пересписание"
        PROVIDER_MISMATCH = "provider_mismatch", "Расхождение провайдера"
        MANUAL_REVIEW = "manual_review", "Ручная проверка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation_id = models.UUIDField(unique=True)
    price_version = models.ForeignKey(PriceVersion, on_delete=models.PROTECT)
    estimated_rub = models.DecimalField(max_digits=14, decimal_places=4)
    provider_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    charged_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    fx_snapshot = models.ForeignKey(
        "FxRateSnapshot", on_delete=models.PROTECT, null=True, blank=True
    )
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    model_version_id_snapshot = models.UUIDField(null=True, blank=True)
    operation_type = models.CharField(max_length=32, default="chat")
    expected_provider_cost_rub = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    gross_profit_rub = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    gross_margin_percent = models.DecimalField(
        max_digits=7, decimal_places=3, null=True, blank=True
    )
    reconciliation_status = models.CharField(
        max_length=24,
        choices=ReconciliationStatus.choices,
        default=ReconciliationStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)


class FxRateSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    base_currency = models.CharField(max_length=3)
    quote_currency = models.CharField(max_length=3, default="RUB")
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    source = models.CharField(max_length=80)
    source_reference = models.CharField(max_length=240, blank=True)
    effective_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["base_currency", "quote_currency", "source", "effective_at"],
                name="unique_fx_rate_source_moment",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and FxRateSnapshot.objects.filter(pk=self.pk).exists():
            raise ValidationError("FX snapshots immutable")
        self.base_currency = self.base_currency.upper()
        self.quote_currency = self.quote_currency.upper()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("FX snapshots cannot be deleted")


class MarkupRuleVersion(models.Model):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Глобальная"
        PROVIDER = "provider", "Провайдер"
        MODEL = "model", "Модель"
        OPERATION = "operation", "Операция"
        ORGANIZATION = "organization", "Организация"
        CONTRACT = "contract", "Договор"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope_type = models.CharField(max_length=20, choices=Scope.choices)
    scope_key = models.CharField(max_length=120, blank=True)
    markup_percent = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    price_multiplier = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(db_index=True)
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-created_at"]
        indexes = [models.Index(fields=["scope_type", "scope_key", "effective_from"])]

    def clean(self):
        if self.scope_type == self.Scope.GLOBAL and self.scope_key:
            raise ValidationError("Global markup rule не должен иметь scope_key")
        if self.scope_type != self.Scope.GLOBAL and not self.scope_key:
            raise ValidationError("Для scoped markup rule обязателен scope_key")
        if self.markup_percent is not None and self.markup_percent < 0:
            raise ValidationError("Markup не может быть отрицательным")
        if self.price_multiplier <= 0:
            raise ValidationError("Price multiplier должен быть положительным")

    def save(self, *args, **kwargs):
        if self.pk and MarkupRuleVersion.objects.filter(pk=self.pk).exists():
            raise ValidationError("Markup rule versions immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Markup rule versions cannot be deleted")


class MarginPolicyVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    minimum_gross_margin_percent = models.DecimalField(max_digits=7, decimal_places=3, default=25)
    anomaly_cost_deviation_percent = models.DecimalField(max_digits=7, decimal_places=3, default=20)
    reconciliation_threshold_rub = models.DecimalField(max_digits=14, decimal_places=4, default=1)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and MarginPolicyVersion.objects.filter(pk=self.pk).exists():
            raise ValidationError("Margin policy versions immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Margin policy versions cannot be deleted")


class CostAnomaly(models.Model):
    class Kind(models.TextChoices):
        MARGIN_FLOOR = "margin_floor", "Маржа ниже floor"
        COST_DEVIATION = "cost_deviation", "Отклонение себестоимости"
        LEDGER_MISMATCH = "ledger_mismatch", "Расхождение ledger"
        REQUEST_MISMATCH = "request_mismatch", "Расхождение запроса"
        PROVIDER_MISMATCH = "provider_mismatch", "Расхождение провайдера"

    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        ACKNOWLEDGED = "acknowledged", "Принята"
        RESOLVED = "resolved", "Закрыта"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    severity = models.CharField(max_length=16, default="warning")
    dedupe_key = models.CharField(max_length=180, unique=True)
    model_slug = models.CharField(max_length=120, blank=True)
    provider_slug = models.CharField(max_length=120, blank=True)
    request_cost = models.ForeignKey(
        RequestCost, on_delete=models.PROTECT, null=True, blank=True, related_name="anomalies"
    )
    expected_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    actual_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class BillingReconciliationRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Выполняется"
        SUCCEEDED = "succeeded", "Завершено"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    checked_wallets = models.PositiveIntegerField(default=0)
    checked_requests = models.PositiveIntegerField(default=0)
    discrepancy_count = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class BillingReconciliationItem(models.Model):
    class Status(models.TextChoices):
        OK = "ok", "OK"
        UNDERCHARGED = "undercharged", "Недосписание"
        OVERCHARGED = "overcharged", "Пересписание"
        PROVIDER_MISMATCH = "provider_mismatch", "Расхождение провайдера"
        MANUAL_REVIEW = "manual_review", "Ручная проверка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        BillingReconciliationRun, on_delete=models.CASCADE, related_name="items"
    )
    entity_type = models.CharField(max_length=32)
    entity_id = models.CharField(max_length=128)
    status = models.CharField(max_length=24, choices=Status.choices)
    expected_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    actual_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entity_type", "entity_id"]
