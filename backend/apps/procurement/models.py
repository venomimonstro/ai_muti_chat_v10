import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ProviderFundingAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(
        "ai_registry.Provider",
        on_delete=models.PROTECT,
        related_name="funding_accounts",
    )
    label = models.CharField(max_length=160)
    credential_env = models.CharField(max_length=120)
    currency = models.CharField(max_length=3, default="USD")
    active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    priority = models.PositiveIntegerField(default=100)
    funded_native = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    reserved_native = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    spent_native = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    low_balance_native = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider__name", "priority", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "credential_env"],
                name="unique_provider_procurement_credential_env",
            ),
            models.UniqueConstraint(
                fields=["provider"],
                condition=models.Q(is_default=True),
                name="unique_default_funding_account_per_provider",
            ),
            models.CheckConstraint(condition=models.Q(funded_native__gte=0), name="funding_account_funded_nonnegative"),
            models.CheckConstraint(condition=models.Q(reserved_native__gte=0), name="funding_account_reserved_nonnegative"),
            models.CheckConstraint(condition=models.Q(spent_native__gte=0), name="funding_account_spent_nonnegative"),
            models.CheckConstraint(condition=models.Q(low_balance_native__gte=0), name="funding_account_low_balance_nonnegative"),
            models.CheckConstraint(
                condition=models.Q(funded_native__gte=models.F("reserved_native") + models.F("spent_native")),
                name="funding_account_not_overdrawn",
            ),
        ]

    @property
    def available_native(self):
        return self.funded_native - self.reserved_native - self.spent_native

    def clean(self):
        self.currency = self.currency.upper().strip()
        self.credential_env = self.credential_env.strip()
        if len(self.currency) != 3:
            raise ValidationError("Валюта должна быть в формате ISO 4217, например USD")
        if not self.credential_env:
            raise ValidationError("Укажите имя переменной окружения с API-ключом")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ProviderPurchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ProviderFundingAccount, on_delete=models.PROTECT, related_name="purchases")
    credit_native = models.DecimalField(max_digits=18, decimal_places=6)
    base_cost_rub = models.DecimalField(max_digits=18, decimal_places=4)
    fees_rub = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    total_cash_outlay_rub = models.DecimalField(max_digits=18, decimal_places=4)
    market_fx_rate_rub = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    effective_cost_rub_per_native = models.DecimalField(max_digits=18, decimal_places=8)
    reference = models.CharField(max_length=300, blank=True)
    purchased_at = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_provider_purchases",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchased_at", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(credit_native__gt=0), name="provider_purchase_credit_positive"),
            models.CheckConstraint(condition=models.Q(base_cost_rub__gte=0), name="provider_purchase_base_nonnegative"),
            models.CheckConstraint(condition=models.Q(fees_rub__gte=0), name="provider_purchase_fees_nonnegative"),
            models.CheckConstraint(condition=models.Q(total_cash_outlay_rub__gt=0), name="provider_purchase_total_positive"),
            models.CheckConstraint(condition=models.Q(effective_cost_rub_per_native__gt=0), name="provider_purchase_unit_cost_positive"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Закупки неизменяемы; создайте корректирующую запись")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Закупки нельзя удалять из финансовой истории")


class ProviderSpendReservation(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Активен"
        SETTLED = "settled", "Закрыт"
        RELEASED = "released", "Освобождён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ProviderFundingAccount, on_delete=models.PROTECT, related_name="spend_reservations")
    amount_native = models.DecimalField(max_digits=18, decimal_places=6)
    actual_native = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    source_key = models.CharField(max_length=180, unique=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_native__gt=0), name="provider_spend_reserve_positive"),
            models.CheckConstraint(
                condition=models.Q(actual_native__isnull=True) | models.Q(actual_native__gte=0),
                name="provider_spend_actual_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(actual_native__isnull=True) | models.Q(actual_native__lte=models.F("amount_native")),
                name="provider_spend_actual_not_over_reserved",
            ),
        ]


class ProviderSpend(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(ProviderFundingAccount, on_delete=models.PROTECT, related_name="spends")
    reservation = models.OneToOneField(ProviderSpendReservation, on_delete=models.PROTECT, related_name="spend")
    source_type = models.CharField(max_length=40)
    source_id = models.CharField(max_length=160)
    model_slug = models.CharField(max_length=160, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)
    native_cost = models.DecimalField(max_digits=18, decimal_places=6)
    nominal_cost_rub = models.DecimalField(max_digits=18, decimal_places=4)
    economic_cost_rub = models.DecimalField(max_digits=18, decimal_places=4)
    customer_charge_rub = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    acquisition_unit_cost_rub = models.DecimalField(max_digits=18, decimal_places=8)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["source_type", "source_id"], name="unique_provider_spend_source"),
            models.CheckConstraint(condition=models.Q(native_cost__gte=0), name="provider_spend_native_nonnegative"),
            models.CheckConstraint(condition=models.Q(nominal_cost_rub__gte=0), name="provider_spend_nominal_nonnegative"),
            models.CheckConstraint(condition=models.Q(economic_cost_rub__gte=0), name="provider_spend_economic_nonnegative"),
            models.CheckConstraint(condition=models.Q(customer_charge_rub__gte=0), name="provider_spend_customer_charge_nonnegative"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("История расхода провайдера неизменяема")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Историю расхода провайдера нельзя удалять")


class RetailTokenPriceVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_slug = models.SlugField(max_length=160, db_index=True)
    input_rub_per_million = models.DecimalField(max_digits=18, decimal_places=4)
    output_rub_per_million = models.DecimalField(max_digits=18, decimal_places=4)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(db_index=True)
    reason = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="retail_token_price_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["model_slug", "-effective_from", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(input_rub_per_million__gt=0), name="retail_input_price_positive"),
            models.CheckConstraint(condition=models.Q(output_rub_per_million__gt=0), name="retail_output_price_positive"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Продажная цена неизменяема; создайте новую версию")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Историю продажных цен нельзя удалять")
