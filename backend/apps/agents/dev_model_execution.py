from dataclasses import dataclass

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.billing.pricing import active_price, quote, require_margin
from apps.billing.services import release, reserve, settle

from .accounting import (
    release_agent_provider_spend,
    reserve_agent_provider_spend,
    settle_agent_provider_spend,
)
from .dev_model_fallback import plan_model_attempts
from .dev_provider_retry import generate_with_key_failover


class DevStageCanceled(Exception):
    pass


@dataclass(frozen=True)
class DevGenerationResult:
    result: object
    model: object
    actual_rub: object
    provider_attempts: int
    model_attempts: list


def _release_customer(reservation):
    if reservation:
        release(reservation.id)


def _release_provider(reservation):
    if reservation:
        release_agent_provider_spend(reservation)


def execute_with_model_fallback(
    *,
    run,
    sequence,
    primary_model,
    messages,
    estimated_input_tokens,
    requested_output_tokens,
    remaining_budget_rub,
    is_canceled,
):
    attempts = plan_model_attempts(
        primary_model=primary_model,
        estimated_input_tokens=estimated_input_tokens,
        requested_output_tokens=requested_output_tokens,
        remaining_budget_rub=remaining_budget_rub,
    )
    if not attempts:
        raise ProviderError(
            "Нет доступной модели, одновременно удовлетворяющей reliability, margin и budget policy",
            code="dev_model_fallback_unavailable",
            retryable=False,
        )

    evidence = []
    last_error = None
    for attempt in attempts:
        if is_canceled():
            raise DevStageCanceled()
        model = attempt.model
        rank = attempt.rank
        customer_reservation = None
        provider_reservation = None
        customer_key = f"agent-run:{run.id}:step:{sequence}:model:{rank}"
        provider_key = f"agent:{run.id}:step:{sequence}:model:{rank}"
        source_id = f"{run.id}:step:{sequence}:model:{rank}"
        try:
            customer_reservation = reserve(run.owner, attempt.preflight.user_charge_rub, customer_key)
            provider_reservation = reserve_agent_provider_spend(
                model=model,
                provider_cost_rub=attempt.preflight.provider_cost_rub,
                fx_snapshot=attempt.preflight.fx_snapshot,
                source_key=provider_key,
            )
            if is_canceled():
                _release_customer(customer_reservation)
                customer_reservation = None
                _release_provider(provider_reservation)
                provider_reservation = None
                raise DevStageCanceled()

            result, provider_attempts = generate_with_key_failover(
                model=model,
                messages=messages,
                max_output_tokens=attempt.output_tokens,
                adapter_factory=adapter_for,
            )
            price = active_price(model.slug)
            actual_quote = require_margin(
                quote(
                    price,
                    max(1, result.input_tokens),
                    max(1, result.output_tokens),
                    provider_slug=model.provider.slug,
                    model_slug=model.slug,
                    operation_type="agent",
                )
            )
            actual = min(actual_quote.user_charge_rub, customer_reservation.amount_rub)
            settle_agent_provider_spend(
                reservation=provider_reservation,
                model=model,
                result=result,
                actual_quote=actual_quote,
                source_id=source_id,
                customer_charge=actual,
            )
            provider_reservation = None
            settle(customer_reservation.id, actual)
            customer_reservation = None
            evidence.append(
                {
                    "rank": rank,
                    "model": model.slug,
                    "provider": model.provider.slug,
                    "status": "completed",
                    "provider_attempts": provider_attempts,
                    "estimated_charge_rub": str(attempt.estimated_charge_rub),
                    "actual_charge_rub": str(actual),
                }
            )
            return DevGenerationResult(
                result=result,
                model=model,
                actual_rub=actual,
                provider_attempts=provider_attempts,
                model_attempts=evidence,
            )
        except DevStageCanceled:
            raise
        except ProviderError as exc:
            last_error = exc
            _release_customer(customer_reservation)
            customer_reservation = None
            _release_provider(provider_reservation)
            provider_reservation = None
            evidence.append(
                {
                    "rank": rank,
                    "model": model.slug,
                    "provider": model.provider.slug,
                    "status": "provider_failed",
                    "error_code": str(exc.code or "provider_error")[:120],
                    "retryable": bool(exc.retryable),
                    "estimated_charge_rub": str(attempt.estimated_charge_rub),
                }
            )
            continue
        except Exception:
            _release_customer(customer_reservation)
            _release_provider(provider_reservation)
            raise

    if last_error is not None:
        last_error.model_attempts = evidence
        raise last_error
    raise ProviderError(
        "Dev model fallback exhausted without an executable candidate",
        code="dev_model_fallback_exhausted",
        retryable=False,
    )
