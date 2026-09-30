from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.ai_registry.models import AIModel
from apps.procurement.models import ProviderSpend

from .models import RequestCost
from .pricing import calculate, calculate_from_snapshot
from .reconciliation import record_cost_outcome

ZERO = Decimal("0.0000")
MAX_MARGIN_PERCENT = Decimal("9999.999")
MIN_MARGIN_PERCENT = Decimal("-9999.999")


def install(services_module) -> None:
    """Never release a customer's full reserve after confirmed provider delivery.

    The legacy recovery hook intentionally rejected a settlement when authoritative
    provider usage priced above the pre-authorized reserve. ``release()`` then
    returned the whole reserve even though the platform had already incurred the
    upstream cost. The customer still must never be charged above the authorized
    amount, so the safe invariant is: settle at ``min(calculated, reserved)``.
    """
    raw_consume = services_module._consume_generation_reservation_after_provider_delivery
    if getattr(raw_consume, "_ai_workspace_confirmed_usage_guard", False):
        return

    def consume_generation_reservation_after_provider_delivery(reservation, wallet):
        if raw_consume(reservation, wallet):
            return True

        key = str(reservation.idempotency_key or "")
        if not key.startswith("generation:"):
            return False
        generation_id = key.split(":", 1)[1]
        if not generation_id:
            return False

        request_cost = (
            RequestCost.objects.select_for_update()
            .select_related("price_version")
            .filter(generation_id=generation_id, provider_cost_rub__isnull=False)
            .first()
        )
        if request_cost is None or not (request_cost.input_tokens or request_cost.output_tokens):
            return False
        if request_cost.charged_rub not in {None, ZERO}:
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

        # The raw hook handles every normal case. Reaching this branch with a
        # confirmed request means the only customer-safe recoverable case is an
        # upstream usage overrun beyond the amount authorized before the API call.
        if calculated_charge <= reservation.amount_rub:
            return False

        actual = reservation.amount_rub
        services_module.settle(reservation.id, actual)

        provider_cost = request_cost.provider_cost_rub or ZERO
        overhead_percent = Decimal(
            (request_cost.pricing_snapshot or {}).get("overhead_total_percent", "0")
        )
        economic_cost = provider_cost * (
            Decimal("1") + overhead_percent / Decimal("100")
        )
        gross_profit = actual - economic_cost
        raw_margin = (gross_profit / actual * Decimal("100")) if actual else ZERO
        gross_margin = min(MAX_MARGIN_PERCENT, max(MIN_MARGIN_PERCENT, raw_margin))

        RequestCost.objects.filter(pk=request_cost.pk).update(
            charged_rub=actual,
            gross_profit_rub=gross_profit,
            gross_margin_percent=gross_margin,
        )
        request_cost.charged_rub = actual
        request_cost.gross_profit_rub = gross_profit
        request_cost.gross_margin_percent = gross_margin
        ProviderSpend.objects.filter(
            source_type="chat",
            source_id=str(request_cost.id),
        ).update(customer_charge_rub=actual)

        model = (
            AIModel.objects.filter(slug=request_cost.price_version.model_slug)
            .select_related("provider")
            .first()
        )
        if model is not None:
            record_cost_outcome(request_cost, model=model)

        # Keep durable chat diagnostics aligned with the immutable wallet ledger.
        try:
            from apps.chat.models import Generation

            Generation.objects.filter(pk=request_cost.generation_id).update(
                actual_cost_rub=actual,
                input_tokens=request_cost.input_tokens,
                output_tokens=request_cost.output_tokens,
            )
        except Exception:
            pass
        return True

    consume_generation_reservation_after_provider_delivery._ai_workspace_confirmed_usage_guard = True
    consume_generation_reservation_after_provider_delivery._raw_consume = raw_consume
    services_module._consume_generation_reservation_after_provider_delivery = (
        consume_generation_reservation_after_provider_delivery
    )
