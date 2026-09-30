from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.billing import services as billing_services
from apps.billing.models import RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot
from apps.procurement.models import ProviderSpend

MONEY_ZERO = Decimal("0.0000")
MAX_MARGIN_PERCENT = Decimal("9999.999")
MIN_MARGIN_PERCENT = Decimal("-9999.999")


def _consume_generation_reservation_after_provider_delivery(reservation, wallet):
    """Settle confirmed provider usage without ever creating customer debt.

    This is the authoritative crash/error recovery path for chat generations.
    If upstream usage is already persisted, a generic failure must not release the
    full customer reservation as though the provider call never happened.

    When calculated retail charge exceeds the pre-authorized reservation, the
    customer is charged at most the reservation amount. The platform records the
    resulting negative margin/cost anomaly instead of hiding provider spend.
    """
    import uuid

    key = str(reservation.idempotency_key or "")
    if not key.startswith("generation:"):
        return False
    generation_id = key.split(":", 1)[1]
    if not generation_id:
        return False
    try:
        generation_uuid = uuid.UUID(generation_id)
    except (ValueError, TypeError, AttributeError):
        return False

    request_cost = (
        RequestCost.objects.select_for_update()
        .select_related("price_version")
        .filter(generation_id=generation_uuid, provider_cost_rub__isnull=False)
        .first()
    )
    if request_cost is None or not (request_cost.input_tokens or request_cost.output_tokens):
        return False
    if request_cost.charged_rub not in {None, MONEY_ZERO}:
        return False

    if request_cost.pricing_snapshot:
        _provider_cost, calculated_charge, _profit, _margin = calculate_from_snapshot(
            request_cost.price_version,
            request_cost.input_tokens,
            request_cost.output_tokens,
            request_cost.pricing_snapshot,
        )
    else:
        _provider_cost, calculated_charge = calculate(
            request_cost.price_version,
            request_cost.input_tokens,
            request_cost.output_tokens,
        )

    # Customer authorization is a hard ceiling. Confirmed upstream usage is not a
    # reason to create debt, but it is also not a reason to pretend the call was free.
    actual = min(max(calculated_charge, MONEY_ZERO), reservation.amount_rub)
    billing_services.settle(reservation.id, actual)

    provider_cost = request_cost.provider_cost_rub or MONEY_ZERO
    gross_profit = (actual - provider_cost).quantize(Decimal("0.0001"))
    raw_margin = (gross_profit / actual * Decimal("100")) if actual else MONEY_ZERO
    gross_margin = min(MAX_MARGIN_PERCENT, max(MIN_MARGIN_PERCENT, raw_margin)).quantize(
        Decimal("0.001")
    )
    RequestCost.objects.filter(pk=request_cost.pk).update(
        charged_rub=actual,
        gross_profit_rub=gross_profit,
        gross_margin_percent=gross_margin,
    )
    ProviderSpend.objects.filter(
        source_type="chat",
        source_id=str(request_cost.id),
    ).update(customer_charge_rub=actual)
    return True


def install() -> None:
    current = billing_services._consume_generation_reservation_after_provider_delivery
    if getattr(current, "_ai_workspace_safe_confirmed_usage", False):
        return
    _consume_generation_reservation_after_provider_delivery._ai_workspace_safe_confirmed_usage = True
    billing_services._consume_generation_reservation_after_provider_delivery = (
        _consume_generation_reservation_after_provider_delivery
    )
