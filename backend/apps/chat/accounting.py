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


def procurement_fail_closed() -> bool:
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def _configured(model) -> bool:
    return ProviderFundingAccount.objects.filter(
        provider=model.provider,
        active=True,
        is_default=True,
    ).exists()


def reserve_chat_provider_spend(*, model, provider_cost_rub, fx_snapshot, source_key):
    """Reserve purchased provider capacity for one chat route candidate.

    Customer-wallet reservation and provider procurement reservation are separate
    ledgers. Both must succeed before paid external generation starts. Legacy
    providers without procurement configuration remain supported only when the
    deployment has not enabled fail-closed procurement runtime yet.
    """
    if not _configured(model):
        if procurement_fail_closed():
            raise ValidationError(
                f"Для провайдера {model.provider.slug} не настроен закупочный аккаунт"
            )
        return None
    if not fx_snapshot or Decimal(fx_snapshot.rate) <= 0:
        if procurement_fail_closed():
            raise ValidationError("Не удалось определить валютный курс закупочного расхода")
        return None
    native = (Decimal(provider_cost_rub) / Decimal(fx_snapshot.rate)).quantize(
        NATIVE_STEP,
        rounding=ROUND_UP,
    )
    if native <= 0:
        return None
    return reserve_provider_spend(
        provider=model.provider,
        amount_native=native,
        source_key=source_key,
    )


def settle_chat_provider_spend(
    *,
    reservation,
    model,
    completed,
    provider_cost_rub,
    fx_snapshot,
    source_id,
    customer_charge_rub,
):
    if not reservation:
        return None
    if not fx_snapshot or Decimal(fx_snapshot.rate) <= 0:
        raise ValidationError("Не удалось определить валютный курс фактического расхода")
    native = (Decimal(provider_cost_rub) / Decimal(fx_snapshot.rate)).quantize(
        NATIVE_STEP,
        rounding=ROUND_UP,
    )
    return settle_provider_spend(
        reservation_id=reservation.id,
        actual_native=native,
        nominal_cost_rub=provider_cost_rub,
        customer_charge_rub=customer_charge_rub,
        source_type="chat",
        source_id=str(source_id),
        model_slug=model.slug,
        provider_request_id=completed.provider_request_id,
        input_tokens=completed.input_tokens,
        output_tokens=completed.output_tokens,
    )


def release_chat_provider_spend(reservation):
    if not reservation:
        return None
    try:
        return release_provider_spend(reservation.id)
    except Exception:
        logger.exception("Chat provider reservation release failed id=%s", reservation.id)
        if procurement_fail_closed():
            raise
        return None
