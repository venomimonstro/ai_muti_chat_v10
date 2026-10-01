from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save
from django.dispatch import receiver

from apps.billing.models import RequestCost


@receiver(pre_save, sender=RequestCost)
def prevent_route_rebind_after_provider_usage(sender, instance, **kwargs):
    """Prevent a stale fallback worker from rebinding cost after provider usage.

    The streaming loop may hold a RequestCost instance while a provider-delivery
    checkpoint persists authoritative upstream usage in another transaction. If the
    stale instance then switches price_version for a fallback candidate, procurement
    could otherwise move the provider reservation away from the model that actually
    incurred the cost. Once usage is confirmed, model/price identity is immutable.
    """
    if not instance.pk:
        return
    current = (
        RequestCost.objects.filter(pk=instance.pk)
        .values("price_version_id", "provider_cost_rub", "input_tokens", "output_tokens")
        .first()
    )
    if not current:
        return
    usage_confirmed = current["provider_cost_rub"] is not None or bool(
        current["input_tokens"] or current["output_tokens"]
    )
    if usage_confirmed and current["price_version_id"] != instance.price_version_id:
        raise ValidationError(
            "Нельзя переключить модель после подтверждённого расхода провайдера"
        )
