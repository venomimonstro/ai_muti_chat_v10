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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation_id = models.UUIDField(unique=True)
    price_version = models.ForeignKey(PriceVersion, on_delete=models.PROTECT)
    estimated_rub = models.DecimalField(max_digits=14, decimal_places=4)
    provider_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    charged_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
