import logging
from decimal import Decimal

from django.db import transaction

from apps.ai_registry.models import AIModel
from apps.ai_registry.token_estimator import estimate_message_tokens, estimate_text_tokens
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.pricing import calculate, calculate_from_snapshot
from apps.billing.reconciliation import record_cost_outcome
from apps.billing.services import release, settle

logger = logging.getLogger(__name__)
VISION_ESTIMATE_TOKENS_PER_IMAGE = 2048


@transaction.atomic
def settle_delivered_partial(generation, text: str):
    """Settle a cancelled request only for content already delivered to the user.

    Provider final usage may be unavailable after a client-side cancellation. We therefore use
    the same calibrated token estimator and immutable pricing snapshot used for preflight, cap
    the result at the pre-authorized reservation, and never create customer debt.
    """
    if not text:
        release(generation.reservation_id)
        return Decimal("0.0000")

    reservation = BalanceReservation.objects.select_for_update().get(pk=generation.reservation_id)
    request_cost = (
        RequestCost.objects.select_for_update()
        .select_related("price_version")
        .get(generation_id=generation.id)
    )
    messages = generation.context_snapshot.get("provider_messages") or [
        {"role": generation.user_message.role, "content": generation.user_message.content}
    ]
    input_tokens = estimate_message_tokens(messages)
    input_tokens += len(generation.context_snapshot.get("vision_assets") or []) * VISION_ESTIMATE_TOKENS_PER_IMAGE
    output_tokens = estimate_text_tokens(text)

    if request_cost.pricing_snapshot:
        provider_cost, charge, gross_profit, gross_margin = calculate_from_snapshot(
            request_cost.price_version,
            input_tokens,
            output_tokens,
            request_cost.pricing_snapshot,
        )
    else:
        provider_cost, charge = calculate(request_cost.price_version, input_tokens, output_tokens)
        gross_profit = charge - provider_cost
        gross_margin = gross_profit / charge * 100 if charge else Decimal("100")

    charge = min(charge, reservation.amount_rub)
    if charge < 0:
        charge = Decimal("0")
    if charge:
        settle(reservation.id, charge)
    else:
        release(reservation.id)

    request_cost.provider_cost_rub = provider_cost
    request_cost.charged_rub = charge
    request_cost.input_tokens = input_tokens
    request_cost.output_tokens = output_tokens
    request_cost.gross_profit_rub = charge - provider_cost
    request_cost.gross_margin_percent = (
        (charge - provider_cost) / charge * 100 if charge else Decimal("0")
    )
    request_cost.save(
        update_fields=[
            "provider_cost_rub",
            "charged_rub",
            "input_tokens",
            "output_tokens",
            "gross_profit_rub",
            "gross_margin_percent",
        ]
    )

    model = AIModel.objects.filter(slug=request_cost.price_version.model_slug).select_related("provider").first()
    if model is not None:
        record_cost_outcome(request_cost, model=model)
        generation.routed_model = model.slug
        generation.provider_slug = model.provider.slug

    generation.input_tokens = input_tokens
    generation.output_tokens = output_tokens
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
        "Settled user-cancelled generation id=%s delivered_chars=%s estimated_charge=%s",
        generation.id,
        len(text),
        charge,
    )
    return charge
