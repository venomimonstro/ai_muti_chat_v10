from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ai_registry.models import Provider
from apps.b2b_api.models import APIUsage
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration

from .models import CostAnomaly

ZERO = Decimal("0")


def _trip_provider(*, provider, model_slug, source, source_id, provider_cost, charged):
    if provider_cost is None or charged is None or provider_cost <= charged:
        return False
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"loss-watchdog:{source}:{source_id}",
        defaults={
            "kind": CostAnomaly.Kind.MARGIN_FLOOR,
            "severity": "critical",
            "model_slug": model_slug,
            "provider_slug": provider.slug,
            "expected_rub": charged,
            "actual_rub": provider_cost,
            "details": {
                "reason": "provider_cost_exceeds_customer_charge",
                "source": source,
                "source_id": str(source_id),
            },
        },
    )
    Provider.objects.filter(pk=provider.pk).update(
        emergency_disabled=True,
        health_state=Provider.HealthState.DISABLED,
    )
    return True


@receiver(post_save, sender=APIUsage, dispatch_uid="billing_loss_watchdog_api_usage")
def watch_b2b_usage(sender, instance, **kwargs):
    if instance.state != APIUsage.State.COMPLETED:
        return
    model = instance.model
    _trip_provider(
        provider=model.provider,
        model_slug=model.slug,
        source="b2b_api",
        source_id=instance.id,
        provider_cost=instance.provider_cost_rub,
        charged=instance.charged_rub,
    )


@receiver(post_save, sender=CompareVariant, dispatch_uid="billing_loss_watchdog_compare")
def watch_compare_variant(sender, instance, **kwargs):
    if instance.state != CompareVariant.State.COMPLETED:
        return
    model = instance.model
    _trip_provider(
        provider=model.provider,
        model_slug=model.slug,
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
    model = instance.model
    _trip_provider(
        provider=model.provider,
        model_slug=model.slug,
        source="images",
        source_id=instance.id,
        provider_cost=instance.provider_cost_rub,
        charged=instance.actual_cost_rub,
    )
