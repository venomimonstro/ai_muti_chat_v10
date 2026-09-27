import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import enforce_spend_limits, notify_low_balance

from .models import AdminBalanceAdjustment, BalanceReservation, LedgerEntry, Wallet

MONEY_ZERO = Decimal("0.0000")
MAX_MARGIN_PERCENT = Decimal("9999.999")
MIN_MARGIN_PERCENT = Decimal("-9999.999")


def _entry(
    wallet,
    kind,
    amount,
    available_delta,
    reserved_delta,
    paid_delta,
    promo_delta,
    source_type,
    source_id,
    key,
):
    return LedgerEntry.objects.create(
        wallet=wallet,
        kind=kind,
        amount_rub=amount,
        available_delta_rub=available_delta,
        reserved_delta_rub=reserved_delta,
        paid_delta_rub=paid_delta,
        promo_delta_rub=promo_delta,
        available_after_rub=wallet.available_rub,
        reserved_after_rub=wallet.reserved_rub,
        source_type=source_type,
        source_id=str(source_id),
        idempotency_key=key,
    )


def _is_public_api(key):
    return str(key).startswith("public-api:")


def _enforce_consumer_operation_velocity(wallet, key):
    if _is_public_api(key):
        return
    limit = max(1, int(os.getenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "20")))
    since = timezone.now() - timedelta(minutes=1)
    recent = (
        wallet.entries.filter(
            kind=LedgerEntry.Kind.RESERVE,
            created_at__gte=since,
            idempotency_key__startswith="reserve:",
        )
        .exclude(idempotency_key__startswith="reserve:public-api:")
        .count()
    )
    if recent >= limit:
        raise ValidationError(
            "Слишком много AI-операций за короткое время. Подождите минуту и повторите запрос."
        )


@transaction.atomic
def credit(user, amount: Decimal, source_type: str, source_id: str, *, bucket="paid"):
    if amount <= 0:
        raise ValidationError("Credit must be positive")
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    key = f"credit:{source_type}:{source_id}"
    existing = LedgerEntry.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    wallet.available_rub += amount
    if bucket == "paid":
        wallet.paid_rub += amount
        paid_delta, promo_delta = amount, MONEY_ZERO
    elif bucket == "promo":
        wallet.promo_rub += amount
        paid_delta, promo_delta = MONEY_ZERO, amount
    else:
        raise ValidationError("Unknown wallet bucket")
    wallet.save(update_fields=["available_rub", "paid_rub", "promo_rub", "updated_at"])
    return _entry(
        wallet,
        LedgerEntry.Kind.CREDIT,
        amount,
        amount,
        MONEY_ZERO,
        paid_delta,
        promo_delta,
        source_type,
        source_id,
        key,
    )


@transaction.atomic
def reserve(user, amount: Decimal, key: str):
    if amount <= 0:
        raise ValidationError("Reserve must be positive")
    existing = BalanceReservation.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    existing = BalanceReservation.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.wallet_id != wallet.id:
            raise ValidationError("Idempotency-Key уже используется другим кошельком")
        return existing
    _enforce_consumer_operation_velocity(wallet, key)
    if not _is_public_api(key):
        enforce_spend_limits(wallet, amount)
    if wallet.available_rub < amount:
        raise ValidationError("Недостаточно средств")
    promo_amount = min(wallet.promo_rub, amount)
    paid_amount = amount - promo_amount
    if wallet.paid_rub < paid_amount:
        raise ValidationError("Wallet bucket invariant violated")
    wallet.available_rub -= amount
    wallet.reserved_rub += amount
    wallet.promo_rub -= promo_amount
    wallet.paid_rub -= paid_amount
    wallet.save(
        update_fields=["available_rub", "reserved_rub", "paid_rub", "promo_rub", "updated_at"]
    )
    reservation = BalanceReservation.objects.create(
        wallet=wallet,
        amount_rub=amount,
        paid_amount_rub=paid_amount,
        promo_amount_rub=promo_amount,
        idempotency_key=key,
    )
    _entry(
        wallet,
        LedgerEntry.Kind.RESERVE,
        amount,
        -amount,
        amount,
        -paid_amount,
        -promo_amount,
        "generation",
        reservation.id,
        f"reserve:{key}",
    )
    return reservation


@transaction.atomic
def settle(reservation_id, actual: Decimal):
    reservation = (
        BalanceReservation.objects.select_for_update()
        .select_related("wallet")
        .get(pk=reservation_id)
    )
    if reservation.state != BalanceReservation.State.ACTIVE:
        return reservation
    if actual < 0 or actual > reservation.amount_rub:
        raise ValidationError("Actual cost must be within reserved amount")
    wallet = Wallet.objects.select_for_update().get(pk=reservation.wallet_id)
    release_amount = reservation.amount_rub - actual
    promo_consumed = min(reservation.promo_amount_rub, actual)
    paid_consumed = actual - promo_consumed
    promo_release = reservation.promo_amount_rub - promo_consumed
    paid_release = reservation.paid_amount_rub - paid_consumed
    wallet.reserved_rub -= reservation.amount_rub
    wallet.available_rub += release_amount
    wallet.promo_rub += promo_release
    wallet.paid_rub += paid_release
    if wallet.reserved_rub < MONEY_ZERO:
        raise ValidationError("Reserved balance invariant violated")
    wallet.save(
        update_fields=["available_rub", "reserved_rub", "paid_rub", "promo_rub", "updated_at"]
    )
    if actual:
        _entry(
            wallet,
            LedgerEntry.Kind.DEBIT,
            actual,
            MONEY_ZERO,
            -actual,
            MONEY_ZERO,
            MONEY_ZERO,
            "generation",
            reservation.id,
            f"settle:{reservation.id}",
        )
    if release_amount:
        _entry(
            wallet,
            LedgerEntry.Kind.RELEASE,
            release_amount,
            release_amount,
            -release_amount,
            paid_release,
            promo_release,
            "generation",
            reservation.id,
            f"release:{reservation.id}",
        )
    reservation.actual_rub = actual
    reservation.state = BalanceReservation.State.SETTLED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["actual_rub", "state", "settled_at"])
    notify_low_balance(wallet)
    return reservation


def _consume_generation_reservation_after_provider_delivery(reservation, wallet):
    """Recover interrupted customer settlement from confirmed provider usage."""
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

    from apps.billing.models import RequestCost
    from apps.billing.pricing import calculate, calculate_from_snapshot
    from apps.procurement.models import ProviderSpend

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

    # Never turn an upstream usage anomaly into customer debt. If authoritative
    # usage exceeds the amount pre-authorized before the provider call, release
    # the customer's full reserve; procurement/anomaly accounting handles the loss.
    if calculated_charge > reservation.amount_rub:
        return False
    actual = max(calculated_charge, MONEY_ZERO)

    settle(reservation.id, actual)

    provider_cost = request_cost.provider_cost_rub or MONEY_ZERO
    gross_profit = actual - provider_cost
    raw_margin = (gross_profit / actual * Decimal("100")) if actual else MONEY_ZERO
    gross_margin = min(MAX_MARGIN_PERCENT, max(MIN_MARGIN_PERCENT, raw_margin))
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


@transaction.atomic
def release(reservation_id):
    reservation = (
        BalanceReservation.objects.select_for_update()
        .select_related("wallet")
        .get(pk=reservation_id)
    )
    if reservation.state != BalanceReservation.State.ACTIVE:
        return reservation
    wallet = Wallet.objects.select_for_update().get(pk=reservation.wallet_id)
    if _consume_generation_reservation_after_provider_delivery(reservation, wallet):
        reservation.refresh_from_db(fields=["actual_rub", "state", "settled_at"])
        return reservation
    wallet.reserved_rub -= reservation.amount_rub
    wallet.available_rub += reservation.amount_rub
    wallet.paid_rub += reservation.paid_amount_rub
    wallet.promo_rub += reservation.promo_amount_rub
    wallet.save(
        update_fields=["available_rub", "reserved_rub", "paid_rub", "promo_rub", "updated_at"]
    )
    _entry(
        wallet,
        LedgerEntry.Kind.RELEASE,
        reservation.amount_rub,
        reservation.amount_rub,
        -reservation.amount_rub,
        reservation.paid_amount_rub,
        reservation.promo_amount_rub,
        "generation",
        reservation.id,
        f"failure-release:{reservation.id}",
    )
    reservation.state = BalanceReservation.State.RELEASED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["state", "settled_at"])
    return reservation


def reconstruct(wallet):
    entries = wallet.entries.order_by("created_at", "id")
    available = sum((entry.available_delta_rub for entry in entries), MONEY_ZERO)
    reserved = sum((entry.reserved_delta_rub for entry in entries), MONEY_ZERO)
    return available, reserved


def reconstruct_buckets(wallet):
    entries = wallet.entries.order_by("created_at", "id")
    paid = sum((entry.paid_delta_rub for entry in entries), MONEY_ZERO)
    promo = sum((entry.promo_delta_rub for entry in entries), MONEY_ZERO)
    return paid, promo


@transaction.atomic
def admin_adjust(*, user, amount: Decimal, key: str, comment: str):
    if not key or not comment.strip():
        raise ValidationError("Admin adjustment requires key and comment")
    existing = AdminBalanceAdjustment.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    existing = AdminBalanceAdjustment.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.wallet_id != wallet.id:
            raise ValidationError("Idempotency-Key уже используется другим кошельком")
        return existing
    if amount == 0:
        raise ValidationError("Adjustment amount cannot be zero")
    paid_delta = MONEY_ZERO
    promo_delta = MONEY_ZERO
    if amount > 0:
        wallet.available_rub += amount
        wallet.promo_rub += amount
        promo_delta = amount
    else:
        debit = -amount
        if wallet.available_rub < debit:
            raise ValidationError("Adjustment cannot overdraw wallet")
        promo_debit = min(wallet.promo_rub, debit)
        paid_debit = debit - promo_debit
        wallet.available_rub -= debit
        wallet.promo_rub -= promo_debit
        wallet.paid_rub -= paid_debit
        promo_delta = -promo_debit
        paid_delta = -paid_debit
    wallet.save(update_fields=["available_rub", "paid_rub", "promo_rub", "updated_at"])
    adjustment = AdminBalanceAdjustment.objects.create(
        wallet=wallet,
        amount_rub=amount,
        comment=comment.strip(),
        idempotency_key=key,
    )
    _entry(
        wallet,
        LedgerEntry.Kind.ADJUSTMENT,
        abs(amount),
        amount,
        MONEY_ZERO,
        paid_delta,
        promo_delta,
        "admin_adjustment",
        adjustment.id,
        f"admin-adjust:{key}",
    )
    return adjustment
