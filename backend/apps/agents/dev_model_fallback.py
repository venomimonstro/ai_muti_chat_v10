import os
from dataclasses import dataclass
from decimal import Decimal

from apps.ai_registry.reliability import candidate_models
from apps.billing.pricing import active_price, quote, require_margin


FALLBACK_PRICE_MULTIPLIER = Decimal(os.getenv("DEV_FALLBACK_PRICE_MULTIPLIER", "1.50"))
MAX_MODEL_ATTEMPTS = max(1, min(int(os.getenv("DEV_MODEL_FALLBACK_ATTEMPTS", "3")), 5))


@dataclass(frozen=True)
class DevModelAttempt:
    model: object
    output_tokens: int
    preflight: object
    estimated_charge_rub: Decimal
    rank: int


def _preflight_for(model, *, estimated_input_tokens, requested_output_tokens):
    output_tokens = min(max(1, int(requested_output_tokens)), int(model.max_output_tokens))
    price = active_price(model.slug)
    preflight = require_margin(
        quote(
            price,
            max(1, int(estimated_input_tokens)),
            output_tokens,
            provider_slug=model.provider.slug,
            model_slug=model.slug,
            operation_type="agent",
        )
    )
    return output_tokens, preflight


def plan_model_attempts(
    *,
    primary_model,
    estimated_input_tokens,
    requested_output_tokens,
    remaining_budget_rub,
    max_attempts=None,
):
    """Build an economically safe ordered model failover plan.

    Every model gets its own price/margin quote. A fallback is never allowed to
    reuse the primary model's reservation assumptions. The caller must reserve
    and settle independently for the selected candidate.
    """
    remaining = max(Decimal("0"), Decimal(str(remaining_budget_rub)))
    limit = max(1, min(int(max_attempts or MAX_MODEL_ATTEMPTS), 5))
    primary_output, primary_quote = _preflight_for(
        primary_model,
        estimated_input_tokens=estimated_input_tokens,
        requested_output_tokens=requested_output_tokens,
    )
    primary_charge = Decimal(primary_quote.user_charge_rub)
    ceiling = primary_charge * max(Decimal("1"), FALLBACK_PRICE_MULTIPLIER)

    available = candidate_models(primary_model)
    attempts = []
    for model in available:
        output_tokens, preflight = _preflight_for(
            model,
            estimated_input_tokens=estimated_input_tokens,
            requested_output_tokens=requested_output_tokens,
        )
        charge = Decimal(preflight.user_charge_rub)
        if charge > remaining:
            continue
        if model.pk != primary_model.pk and charge > ceiling:
            continue
        attempts.append(
            DevModelAttempt(
                model=model,
                output_tokens=output_tokens,
                preflight=preflight,
                estimated_charge_rub=charge,
                rank=len(attempts) + 1,
            )
        )
        if len(attempts) >= limit:
            break

    # candidate_models() can exclude an unhealthy primary. If no safe fallback
    # remains, return an empty plan; execution must fail closed rather than call
    # an unavailable or economically invalid model.
    return attempts
