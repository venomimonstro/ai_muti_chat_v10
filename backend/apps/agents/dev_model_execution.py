from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry import dispatch
from apps.billing.pricing import active_price
from apps.billing.services import release, reserve, settle

from .accounting import (
    actual_agent_quote_from_snapshot,
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


def _reservation_evidence(reservation):
    return str(getattr(reservation, "id", "") or "")


def _settlement_interrupted_error(
    *,
    evidence,
    model,
    attempt,
    customer_reservation,
    provider_reservation,
    result,
    cause_code,
):
    evidence.append(
        {
            "rank": attempt.rank,
            "model": model.slug,
            "provider": model.provider.slug,
            "status": "settlement_interrupted",
            "error_code": str(cause_code or "settlement_error")[:120],
            "customer_reservation_id": _reservation_evidence(customer_reservation),
            "provider_reservation_id": _reservation_evidence(provider_reservation),
            "price_version_id": str(getattr(attempt.price, "id", "") or ""),
            "provider_request_id": str(getattr(result, "provider_request_id", "") or "")[:200],
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "estimated_charge_rub": str(attempt.estimated_charge_rub),
        }
    )
    error = ProviderError(
        "Провайдер вернул результат, но финансовое закрытие шага прервано; требуется reconciliation",
        code="dev_settlement_interrupted",
        retryable=False,
    )
    error.model_attempts = evidence
    return error


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
        provider_delivered = False
        result = None
        customer_key = f"agent-run:{run.id}:step:{sequence}:model:{rank}"
        provider_key = f"agent:{run.id}:step:{sequence}:model:{rank}"
        source_id = f"{run.id}:step:{sequence}:model:{rank}"
        try:
            customer_reservation = reserve(run.owner, attempt.preflight.user_charge_rub, customer_key)
            provider_currency = str(
                (getattr(attempt.preflight, "pricing_snapshot", {}) or {}).get(
                    "provider_currency"
                )
                or ""
            )
            provider_reservation = reserve_agent_provider_spend(
                model=model,
                provider_cost_rub=attempt.preflight.provider_cost_rub,
                fx_snapshot=attempt.preflight.fx_snapshot,
                source_key=provider_key,
                provider_currency=provider_currency,
            )
            if is_canceled():
                _release_customer(customer_reservation)
                customer_reservation = None
                _release_provider(provider_reservation)
                provider_reservation = None
                raise DevStageCanceled()

            provider_adapter_attempt = 0

            def reserved_adapter_factory(candidate):
                nonlocal provider_reservation, provider_adapter_attempt
                provider_adapter_attempt += 1

                # record_failure() degrades the exact failed credential before this
                # factory is invoked again. A retry must therefore move the provider
                # reserve to a newly selected HEALTHY funded account; retrying with
                # the old reservation would either reuse a bad key or let execution
                # drift away from the procurement ledger.
                if provider_adapter_attempt > 1 and provider_reservation is not None:
                    _release_provider(provider_reservation)
                    provider_reservation = None
                    provider_reservation = reserve_agent_provider_spend(
                        model=candidate,
                        provider_cost_rub=attempt.preflight.provider_cost_rub,
                        fx_snapshot=attempt.preflight.fx_snapshot,
                        source_key=f"{provider_key}:retry:{provider_adapter_attempt}",
                        provider_currency=provider_currency,
                    )

                funding_account_id = (
                    getattr(provider_reservation, "account_id", None)
                    if provider_reservation is not None
                    else None
                )
                return dispatch.adapter_for(
                    candidate,
                    funding_account_id=funding_account_id,
                )

            result, provider_attempts = generate_with_key_failover(
                model=model,
                messages=messages,
                max_output_tokens=attempt.output_tokens,
                adapter_factory=reserved_adapter_factory,
            )
            provider_delivered = True

            price = attempt.price or active_price(model.slug)
            actual_quote = actual_agent_quote_from_snapshot(
                price=price,
                result=result,
                preflight=attempt.preflight,
            )
            actual = min(actual_quote.user_charge_rub, customer_reservation.amount_rub)

            with transaction.atomic():
                settle_agent_provider_spend(
                    reservation=provider_reservation,
                    model=model,
                    result=result,
                    actual_quote=actual_quote,
                    source_id=source_id,
                    customer_charge=actual,
                )
                settle(customer_reservation.id, actual)
            provider_reservation = None
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
                    "price_version_id": str(getattr(price, "id", "") or ""),
                    "provider_request_id": str(getattr(result, "provider_request_id", "") or "")[:200],
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
            if provider_delivered:
                raise _settlement_interrupted_error(
                    evidence=evidence,
                    model=model,
                    attempt=attempt,
                    customer_reservation=customer_reservation,
                    provider_reservation=provider_reservation,
                    result=result,
                    cause_code=exc.code,
                ) from exc
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
        except ValidationError as exc:
            if provider_delivered:
                raise _settlement_interrupted_error(
                    evidence=evidence,
                    model=model,
                    attempt=attempt,
                    customer_reservation=customer_reservation,
                    provider_reservation=provider_reservation,
                    result=result,
                    cause_code="validation_error",
                ) from exc
            _release_customer(customer_reservation)
            customer_reservation = None
            _release_provider(provider_reservation)
            provider_reservation = None
            evidence.append(
                {
                    "rank": rank,
                    "model": model.slug,
                    "provider": model.provider.slug,
                    "status": "reservation_failed",
                    "error_code": "validation_error",
                    "estimated_charge_rub": str(attempt.estimated_charge_rub),
                }
            )
            last_error = ProviderError(
                "Резервирование кандидата Dev Studio отклонено",
                code="dev_candidate_reservation_failed",
                retryable=True,
            )
            continue
        except Exception as exc:
            if provider_delivered:
                raise _settlement_interrupted_error(
                    evidence=evidence,
                    model=model,
                    attempt=attempt,
                    customer_reservation=customer_reservation,
                    provider_reservation=provider_reservation,
                    result=result,
                    cause_code="internal_error",
                ) from exc
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
