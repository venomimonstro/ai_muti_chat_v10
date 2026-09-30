from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.b2b_api.models import APIUsage
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration

from .models import CostAnomaly

ZERO = Decimal("0")


def _trip_model(*, model, source, source_id, provider_cost, charged):
    """Stop further loss on one model without disabling healthy sibling capacity."""
    provider = model.provider
    if provider_cost is None or charged is None or provider_cost <= charged:
        return False
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"loss-watchdog:{source}:{source_id}",
        defaults={
            "kind": CostAnomaly.Kind.MARGIN_FLOOR,
            "severity": "critical",
            "model_slug": model.slug,
            "provider_slug": provider.slug,
            "expected_rub": charged,
            "actual_rub": provider_cost,
            "details": {
                "reason": "provider_cost_exceeds_customer_charge",
                "source": source,
                "source_id": str(source_id),
                "scope": "model_economics",
            },
        },
    )
    type(model).objects.filter(pk=model.pk).update(enabled=False)
    model.enabled = False
    return True


@receiver(post_save, sender=APIUsage, dispatch_uid="billing_loss_watchdog_api_usage")
def watch_b2b_usage(sender, instance, **kwargs):
    if instance.state != APIUsage.State.COMPLETED:
        return
    _trip_model(
        model=instance.model,
        source="b2b_api",
        source_id=instance.id,
        provider_cost=instance.provider_cost_rub,
        charged=instance.charged_rub,
    )


@receiver(post_save, sender=CompareVariant, dispatch_uid="billing_loss_watchdog_compare")
def watch_compare_variant(sender, instance, **kwargs):
    if instance.state != CompareVariant.State.COMPLETED:
        return
    _trip_model(
        model=instance.model,
        source="compare",
        source_id=instance.id,
        provider_cost=instance.provider_cost_rub,
        charged=instance.actual_cost_rub,
    )


@receiver(post_save, sender=ImageGeneration, dispatch_uid="billing_loss_watchdog_images")
def watch_image_generation(sender, instance, **kwargs):
    if instance.state != ImageGeneration.State.COMPLETED:
        return
    if instance.provider_cost_rub is None or instance.actual_cost_rub is None:
        return
    _trip_model(
        model=instance.model,
        source="images",
        source_id=instance.id,
        provider_cost=instance.provider_cost_rub,
        charged=instance.actual_cost_rub,
    )