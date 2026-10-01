from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import RequestCost


MODEL_UPDATE_FIELDS = {"price_version", "price_version_id"}


def _switches_price(update_fields) -> bool:
    if update_fields is None:
        return True
    return bool(MODEL_UPDATE_FIELDS.intersection(set(update_fields)))


def _assert_rebind_allowed(instance: RequestCost) -> None:
    current = (
        RequestCost.objects.select_for_update()
        .filter(pk=instance.pk)
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


def install() -> None:
    """Serialize RequestCost model switches against provider-delivery settlement.

    A signal-only check is not sufficient: under PostgreSQL MVCC a stale fallback
    worker can read the pre-checkpoint row while another transaction is persisting
    provider usage, then wait and overwrite price_version after that transaction
    commits. Guard the actual model-switching save with SELECT ... FOR UPDATE so both
    operations serialize on the same RequestCost row.
    """
    current_save = RequestCost.save
    if getattr(current_save, "_ai_workspace_usage_rebind_guard", False):
        return

    def save(instance, *args, **kwargs):
        if not instance.pk or not _switches_price(kwargs.get("update_fields")):
            return current_save(instance, *args, **kwargs)
        with transaction.atomic():
            _assert_rebind_allowed(instance)
            return current_save(instance, *args, **kwargs)

    save._ai_workspace_usage_rebind_guard = True
    save._raw_save = current_save
    RequestCost.save = save
