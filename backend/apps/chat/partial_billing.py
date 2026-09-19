import logging
from decimal import Decimal

from django.db import transaction

from apps.ai_registry.models import AIModel
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot
from apps.billing.reconciliation import record_cost_outcome
from apps.billing.services import release, settle
from apps.procurement.models import ProviderSpend

logger = logging.getLogger(__name__)
ZERO = Decimal("0.0000")


@transaction.atomic
def settle_delivered_partial(generation, text: str):
    """Finalize a cancelled/aborted stream without guessing provider usage.

    Customer money is charged only when authoritative provider usage was already
    persisted on RequestCost. If the stream is interrupted before terminal usage
    arrives, the reservation is released in full even if some text reached the
    browser. In that case the platform absorbs any unconfirmed provider cost.
    """
    if not generation.reservation_id:
        return ZERO

    reservation = (
        BalanceReservation.objects.select_for_update()
        .filter(pk=generation.reservation_id)
        .first()
    )
    if reservation is None:
        return ZERO
    if reservation.state != BalanceReservation.State.ACTIVE:
        return reservation.actual_rub or ZERO

    request_cost = (
        RequestCost.objects.select_for_update()
        .select_related("price_version")
        .filter(generation_id=generation.id)
        .first()
    )

    provider_usage_confirmed = bool(
        request_cost is not None
        and request_cost.provider_cost_rub is not None
        and (request_cost.input_tokens or request_cost.output_tokens)
    )
    if not provider_usage_confirmed:
        release(reservation.id)
        logger.info(
            "Released cancelled generation without confirmed provider usage id=%s delivered_chars=%s",
            generation.id,
            len(text or ""),
        )
        return ZERO

    if request_cost.pricing_snapshot:
        _provider_cost_from_snapshot, calculated_charge, _profit, _margin = calculate_from_snapshot(
            request_cost.price_version,
            request_cost.input_tokens,
            request_cost.output_tokens,
            request_cost.pricing_snapshot,
        )
    else:
        _provider_cost_from_snapshot, calculated_charge = calculate(
            request_cost.price_version,
            request_cost.input_tokens,
            request_cost.output_tokens,
        )

    # Never create customer debt and never consume more than the amount authorized
    # before the provider call. A pricing overrun becomes a platform loss/anomaly.
    charge = min(max(calculated_charge, ZERO), reservation.amount_rub)
    if charge:
        settle(reservation.id, charge)
    else:
        release(reservation.id)

    provider_cost = request_cost.provider_cost_rub or ZERO
    gross_profit = charge - provider_cost
    gross_margin = (gross_profit / charge * Decimal("100")) if charge else ZERO

    # Bypass RequestCost post_save procurement hooks: provider usage was already
    # confirmed and procurement was already settled when provider_cost_rub was
    # persisted. This update only completes the customer-side ledger state.
    RequestCost.objects.filter(pk=request_cost.pk).update(
        charged_rub=charge,
        gross_profit_rub=gross_profit,
        gross_margin_percent=gross_margin,
    )
    request_cost.charged_rub = charge
    request_cost.gross_profit_rub = gross_profit
    request_cost.gross_margin_percent = gross_margin
    ProviderSpend.objects.filter(
        source_type="chat",
        source_id=str(request_cost.id),
    ).update(customer_charge_rub=charge)

    model = (
        AIModel.objects.filter(slug=request_cost.price_version.model_slug)
        .select_related("provider")
        .first()
    )
    if model is not None:
        record_cost_outcome(request_cost, model=model)
        generation.routed_model = model.slug
        generation.provider_slug = model.provider.slug

    generation.input_tokens = request_cost.input_tokens
    generation.output_tokens = request_cost.output_tokens
    generation.actual_cost_rub = charge
    generation.save(
        update_fields=[
            "input_tokens",
            "output_tokens",
            "actual_cost_rub",
            "routed_model",
            "provider_slug",
        ]
    )
    logger.info(
        "Settled cancelled generation from confirmed provider usage id=%s charge=%s",
        generation.id,
        charge,
    )
    return charge
