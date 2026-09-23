import logging
from decimal import ROUND_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import (
    release_provider_spend,
    reserve_provider_spend,
    settle_provider_spend,
)

logger = logging.getLogger(__name__)
NATIVE_STEP = Decimal("0.000001")


def procurement_fail_closed():
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def reserve_agent_provider_spend(*, model, provider_cost_rub, fx_snapshot, source_key):
    configured = ProviderFundingAccount.objects.filter(
        provider=model.provider,
        active=True,
        is_default=True,
    ).exists()
    if not configured:
        if procurement_fail_closed():
            raise ValidationError(
                f"Для провайдера {model.provider.slug} не настроен закупочный аккаунт"
            )
        return None
    if not fx_snapshot or fx_snapshot.rate <= 0:
        if procurement_fail_closed():
            raise ValidationError("Не удалось определить валютный курс закупочного расхода")
        return None
    native = (Decimal(provider_cost_rub) / Decimal(fx_snapshot.rate)).quantize(
        NATIVE_STEP,
        rounding=ROUND_UP,
    )
    if native <= 0:
        return None
    try:
        return reserve_provider_spend(
            provider=model.provider,
            amount_native=native,
            source_key=source_key,
        )
    except ValidationError:
        if procurement_fail_closed():
            raise
        logger.exception(
            "Optional agent provider reservation failed provider=%s source=%s",
            model.provider.slug,
            source_key,
        )
        return None


def settle_agent_provider_spend(
    *,
    reservation,
    model,
    result,
    actual_quote,
    source_id,
    customer_charge,
):
    if not reservation:
        return None
    try:
        fx = actual_quote.fx_snapshot
        if not fx or fx.rate <= 0:
            raise ValidationError("Не удалось определить валютный курс фактического расхода")
        native = (Decimal(actual_quote.provider_cost_rub) / Decimal(fx.rate)).quantize(
            NATIVE_STEP,
            rounding=ROUND_UP,
        )
        return settle_provider_spend(
            reservation_id=reservation.id,
            actual_native=native,
            nominal_cost_rub=actual_quote.provider_cost_rub,
            customer_charge_rub=customer_charge,
            source_type="agent",
            source_id=str(source_id),
            model_slug=model.slug,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
    except Exception:
        if procurement_fail_closed():
            raise
        logger.exception(
            "Optional agent provider settlement failed provider=%s source=%s",
            model.provider.slug,
            source_id,
        )
        try:
            release_provider_spend(reservation.id)
        except Exception:
            logger.exception(
                "Optional agent provider reservation release failed provider=%s source=%s",
                model.provider.slug,
                source_id,
            )
        return None


def release_agent_provider_spend(reservation):
    if not reservation:
        return None
    try:
        return release_provider_spend(reservation.id)
    except Exception:
        if procurement_fail_closed():
            raise
        logger.exception("Optional agent provider reservation release failed id=%s", reservation.id)
        return None
