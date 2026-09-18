import re

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .models import CompareVariant, Conversation, Message


def prompt_title(content: str, max_length: int = 72) -> str:
    value = re.sub(r"\s+", " ", content).strip()
    if not value:
        return "Новый чат"
    if len(value) <= max_length:
        return value
    shortened = value[: max_length + 1].rsplit(" ", 1)[0].strip()
    if len(shortened) < 20:
        shortened = value[:max_length].strip()
    return f"{shortened.rstrip('.,;:!?')}…"


@receiver(post_save, sender=Message)
def touch_conversation_on_user_message(sender, instance, created, **kwargs):
    if not created or instance.role != Message.Role.USER:
        return
    updates = {"updated_at": timezone.now()}
    conversation = Conversation.objects.filter(pk=instance.conversation_id).only("title").first()
    if conversation and conversation.title == "Новый чат":
        has_earlier_user_message = Message.objects.filter(
            conversation_id=instance.conversation_id,
            role=Message.Role.USER,
        ).exclude(pk=instance.pk).exists()
        if not has_earlier_user_message:
            updates["title"] = prompt_title(instance.content)
    Conversation.objects.filter(pk=instance.conversation_id).update(**updates)


@receiver(post_save, sender=CompareVariant)
def fail_closed_on_compare_cost_overrun(sender, instance, **kwargs):
    """Stop repeated provider losses if a Compare result exceeds its pre-authorized ceiling."""
    if instance.state != CompareVariant.State.COMPLETED:
        return
    actual = instance.actual_cost_rub
    provider_cost = instance.provider_cost_rub
    if actual is None or provider_cost is None:
        return
    above_reserve = actual > instance.expected_max_rub
    guaranteed_loss = provider_cost > actual
    if not (above_reserve or guaranteed_loss):
        return

    model = instance.model
    reason = "compare_charge_above_reserved_maximum" if above_reserve else "compare_provider_cost_above_customer_charge"
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"compare-critical:{instance.id}",
        defaults={
            "kind": CostAnomaly.Kind.COST_DEVIATION if above_reserve else CostAnomaly.Kind.MARGIN_FLOOR,
            "expected_rub": instance.expected_max_rub if above_reserve else actual,
            "actual_rub": actual if above_reserve else provider_cost,
            "details": {
                "reason": reason,
                "compare_variant_id": str(instance.id),
                "model": model.slug,
                "provider": model.provider.slug,
            },
            "severity": "critical",
            "model_slug": model.slug,
            "provider_slug": model.provider.slug,
        },
    )
    Provider.objects.filter(pk=model.provider_id).update(
        emergency_disabled=True,
        health_state=Provider.HealthState.DISABLED,
    )
