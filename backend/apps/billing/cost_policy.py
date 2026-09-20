from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class PricingOverheadPolicyVersion(models.Model):
    """Versioned global pricing overheads applied on top of provider cost.

    These percentages are pricing reserves, not a tax-accounting engine. They are
    intentionally explicit so admin pricing, client charging and historical
    pricing snapshots use the same assumptions.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tax_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    topup_fee_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    other_expenses_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    refund_withdrawal_percent = models.DecimalField(max_digits=7, decimal_places=3, default=0)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(db_index=True, default=timezone.now)
    reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "billing"
        db_table = "billing_pricingoverheadpolicyversion"
        ordering = ["-effective_from", "-created_at"]

    @property
    def total_percent(self) -> Decimal:
        return (
            self.tax_percent
            + self.topup_fee_percent
            + self.other_expenses_percent
            + self.refund_withdrawal_percent
        )

    def clean(self):
        for field in (
            "tax_percent",
            "topup_fee_percent",
            "other_expenses_percent",
            "refund_withdrawal_percent",
        ):
            value = getattr(self, field)
            if value < 0:
                raise ValidationError({field: "Процент не может быть отрицательным"})
            if value > 100:
                raise ValidationError({field: "Процент не может быть больше 100"})

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Pricing overhead policy versions immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Pricing overhead policy versions cannot be deleted")


def active_pricing_overhead_policy() -> PricingOverheadPolicyVersion:
    item = (
        PricingOverheadPolicyVersion.objects.filter(
            active=True,
            effective_from__lte=timezone.now(),
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if item is None:
        item = PricingOverheadPolicyVersion.objects.create(
            tax_percent=0,
            topup_fee_percent=0,
            other_expenses_percent=0,
            refund_withdrawal_percent=0,
            active=True,
            effective_from=timezone.now(),
            reason="Default zero overhead policy",
        )
    return item
