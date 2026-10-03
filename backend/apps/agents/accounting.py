import logging
from decimal import ROUND_UP, Decimal
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.billing.models import BalanceReservation
from apps.billing.pricing import calculate_from_snapshot
from apps.billing.services import release

from apps.procurement.account_routing import reserve_provider_spend
from apps.procurement.models import ProviderFundingAccount, ProviderSpend
from apps.procurement.services import release_provider_spend, settle_provider_spend

logger = logging.getLogger(__name__)
NATIVE_STEP = Decimal("0.000001")


def procurement_fail_closed():
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def reserve_agent_provider_spend(
    *,
    model,
    provider_cost_rub,
    fx_snapshot,
    source_key,
    provider_currency="",
):
    configured = ProviderFundingAccount.objects.filter(
        provider=model.provider,
        active=True,
    ).exists()
    if not configured:
        # A provider funding ledger is opt-in. Direct admin-managed API keys are
        # valid for Agent/Dev execution until a purchasing account is created;
        # once configured, all procurement reservations remain strict.
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
            currency=str(provider_currency or "").upper().strip(),
            model_upstream=str(model.upstream_model or ""),
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


def actual_agent_quote_from_snapshot(*, price, result, preflight):
    """Price confirmed usage from the exact immutable preflight snapshot."""
    snapshot = dict(getattr(preflight, "pricing_snapshot", {}) or {})
    if not snapshot:
        raise ValidationError("Отсутствует immutable pricing snapshot Agent-запроса")
    provider_cost, charge, gross_profit, gross_margin = calculate_from_snapshot(
        price,
        max(0, int(result.input_tokens or 0)),
        max(0, int(result.output_tokens or 0)),
        snapshot,
    )
    return SimpleNamespace(
        provider_cost_rub=provider_cost,
        user_charge_rub=charge,
        gross_profit_rub=gross_profit,
        gross_margin_percent=gross_margin,
        fx_snapshot=getattr(preflight, "fx_snapshot", None),
        pricing_snapshot=snapshot,
    )


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
        # A result object means the external provider already completed usage.
        # Never release that procurement reservation as "unused" merely because
        # local financial settlement failed. Keep the evidence/reserve intact for
        # reconciliation and fail the caller closed in every environment.
        logger.exception(
            "Agent provider settlement failed after confirmed delivery provider=%s source=%s",
            model.provider.slug,
            source_id,
        )
        raise


def update_agent_provider_customer_charge(spend, customer_charge):
    """Attach customer revenue only after the wallet settlement is durable."""
    if spend is None:
        return None
    charge = Decimal(str(customer_charge or 0)).quantize(Decimal("0.0001"))
    ProviderSpend.objects.filter(pk=spend.pk).update(customer_charge_rub=charge)
    spend.customer_charge_rub = charge
    return spend


def build_agent_provider_delivery_checkpoint(
    *,
    model,
    result,
    actual_quote,
    provider_reservation,
    customer_reservation,
    source_id,
    customer_charge,
):
    """Build durable provider-delivery evidence independent of the owning surface."""
    if provider_reservation is None:
        return None
    fx = actual_quote.fx_snapshot
    if not fx or fx.rate <= 0:
        raise ValidationError("Не удалось сохранить checkpoint расхода без FX")
    return {
        "status": "pending",
        "provider_reservation_id": str(provider_reservation.id),
        "customer_reservation_id": (
            str(customer_reservation.id) if customer_reservation is not None else ""
        ),
        "source_id": str(source_id),
        "model_slug": str(model.slug),
        "provider_request_id": str(result.provider_request_id or "")[:200],
        "input_tokens": max(0, int(result.input_tokens or 0)),
        "output_tokens": max(0, int(result.output_tokens or 0)),
        "provider_cost_rub": str(actual_quote.provider_cost_rub),
        "fx_rate": str(fx.rate),
        "customer_charge_rub": str(customer_charge or 0),
    }


def checkpoint_agent_provider_delivery(
    *,
    step,
    model,
    result,
    actual_quote,
    provider_reservation,
    customer_reservation,
    source_id,
    customer_charge,
):
    """Persist enough evidence to reconcile a crash after provider delivery."""
    if step is None:
        return None
    checkpoint = build_agent_provider_delivery_checkpoint(
        model=model,
        result=result,
        actual_quote=actual_quote,
        provider_reservation=provider_reservation,
        customer_reservation=customer_reservation,
        source_id=source_id,
        customer_charge=customer_charge,
    )
    if checkpoint is None:
        return None
    payload = dict(step.output_payload or {})
    payload["_provider_settlement"] = checkpoint
    step.output_payload = payload
    step.save(update_fields=["output_payload"])
    return checkpoint


def agent_provider_checkpoint_pending(step) -> bool:
    if step is None:
        return False
    payload = getattr(step, "output_payload", {}) or {}
    checkpoint = payload.get("_provider_settlement") or {}
    return checkpoint.get("status") == "pending"


def mark_agent_provider_checkpoint_settled(step, *, provider_spend=None):
    if step is None:
        return
    payload = dict(step.output_payload or {})
    checkpoint = dict(payload.get("_provider_settlement") or {})
    if not checkpoint:
        return
    checkpoint["status"] = "settled"
    if provider_spend is not None:
        checkpoint["provider_spend_id"] = str(provider_spend.id)
    payload["_provider_settlement"] = checkpoint
    step.output_payload = payload
    step.save(update_fields=["output_payload"])


@transaction.atomic
def reconcile_agent_provider_checkpoint_data(checkpoint):
    """Reconcile one raw checkpoint and return the authoritative ProviderSpend."""
    checkpoint = dict(checkpoint or {})
    if checkpoint.get("status") != "pending":
        return None

    reservation_id = checkpoint.get("provider_reservation_id")
    if not reservation_id:
        raise ValidationError("Provider settlement checkpoint lost reservation id")
    reservation = ProviderSpendReservation.objects.select_for_update().filter(
        pk=reservation_id
    ).first()
    if reservation is None:
        raise ValidationError("Provider settlement checkpoint reservation is missing")

    customer_charge = Decimal("0")
    customer_id = checkpoint.get("customer_reservation_id")
    customer = None
    if customer_id:
        customer = BalanceReservation.objects.select_for_update().filter(pk=customer_id).first()
        if customer is not None and customer.state == BalanceReservation.State.SETTLED:
            customer_charge = Decimal(customer.actual_rub or 0)

    fx_rate = Decimal(str(checkpoint.get("fx_rate") or "0"))
    provider_cost = Decimal(str(checkpoint.get("provider_cost_rub") or "0"))
    if fx_rate <= 0 or provider_cost < 0:
        raise ValidationError("Provider settlement checkpoint contains invalid cost data")
    native = (provider_cost / fx_rate).quantize(NATIVE_STEP, rounding=ROUND_UP)

    spend = ProviderSpend.objects.filter(
        source_type="agent",
        source_id=str(checkpoint.get("source_id") or ""),
    ).first()
    if spend is None:
        if reservation.state != ProviderSpendReservation.State.ACTIVE:
            raise ValidationError(
                "Provider settlement checkpoint is closed without ProviderSpend"
            )
        spend = settle_provider_spend(
            reservation_id=reservation.id,
            actual_native=native,
            nominal_cost_rub=provider_cost,
            customer_charge_rub=customer_charge,
            source_type="agent",
            source_id=str(checkpoint.get("source_id") or ""),
            model_slug=str(checkpoint.get("model_slug") or ""),
            provider_request_id=str(checkpoint.get("provider_request_id") or ""),
            input_tokens=max(0, int(checkpoint.get("input_tokens") or 0)),
            output_tokens=max(0, int(checkpoint.get("output_tokens") or 0)),
        )
    elif spend.customer_charge_rub != customer_charge:
        update_agent_provider_customer_charge(spend, customer_charge)

    if customer is not None and customer.state == BalanceReservation.State.ACTIVE:
        release(customer.id)

    return spend


@transaction.atomic
def reconcile_agent_provider_checkpoint(step):
    """Recover confirmed external usage without guessing or charging after a crash."""
    payload = dict(step.output_payload or {})
    checkpoint = dict(payload.get("_provider_settlement") or {})
    if checkpoint.get("status") != "pending":
        return None
    spend = reconcile_agent_provider_checkpoint_data(checkpoint)
    mark_agent_provider_checkpoint_settled(step, provider_spend=spend)
    return spend


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
