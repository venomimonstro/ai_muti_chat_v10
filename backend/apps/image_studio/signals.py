from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .models import ImageGeneration


@receiver(post_save, sender=ImageGeneration)
def fail_closed_on_image_negative_margin(sender, instance, **kwargs):
    if instance.state != ImageGeneration.State.COMPLETED:
        return
    provider_cost = instance.provider_cost_rub
    charged = instance.actual_cost_rub
    if provider_cost is None or charged is None or provider_cost <= charged:
        return

    model = instance.model
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"image-critical-loss:{instance.id}",
        defaults={
            "kind": CostAnomaly.Kind.MARGIN_FLOOR,
            "severity": "critical",
            "model_slug": model.slug,
            "provider_slug": model.provider.slug,
            "expected_rub": charged,
            "actual_rub": provider_cost,
            "details": {
                "reason": "image_provider_cost_above_customer_charge",
                "generation_id": str(instance.id),
                "model": model.slug,
                "provider": model.provider.slug,
            },
        },
    )
    Provider.objects.filter(pk=model.provider_id).update(
        emergency_disabled=True,
        health_state=Provider.HealthState.DISABLED,
    )
