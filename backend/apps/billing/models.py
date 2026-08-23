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
    updated_at = models.DateTimeField(auto_now=True)


class LedgerEntry(models.Model):
    class Kind(models.TextChoices):
        CREDIT = "credit", "Пополнение"
        RESERVE = "reserve", "Резерв"
        RELEASE = "release", "Освобождение"
        DEBIT = "debit", "Списание"
        ADJUSTMENT = "adjustment", "Корректировка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="entries")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount_rub = models.DecimalField(max_digits=14, decimal_places=4)
    available_delta_rub = models.DecimalField(max_digits=14, decimal_places=4)
    reserved_delta_rub = models.DecimalField(max_digits=14, decimal_places=4)
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
    actual_rub = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.ACTIVE)
    idempotency_key = models.CharField(max_length=160, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True)
