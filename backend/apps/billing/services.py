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
    """Recover an interrupted customer settlement from confirmed provider usage.

    This path is deliberately conservative: it never charges the whole reserve.
    It recomputes the exact retail charge from the immutable pricing snapshot and
    confirmed token usage, then caps it at the amount authorized before the call.
    """
    key = str(reservation.idempotency_key or "")
    if not key.startswith("generation:"):
        return False
    generation_id = key.split(":", 1)[1]
    if not generation_id:
        return False

    from apps.billing.models import RequestCost
    from apps.billing.pricing import calculate, calculate_from_snapshot
    from apps.procurement.models import ProviderSpend

    request_cost = (
        RequestCost.objects.select_for_update()
        .select_related("price_version")
        .filter(generation_id=generation_id, provider_cost_rub__isnull=False)
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
    actual = min(max(calculated_charge, MONEY_ZERO), reservation.amount_rub)

    # settle() releases the unused part of the reserve and consumes only `actual`.
    settle(reservation.id, actual)

    provider_cost = request_cost.provider_cost_rub or MONEY_ZERO
    gross_profit = actual - provider_cost
    gross_margin = (gross_profit / actual * Decimal("100")) if actual else MONEY_ZERO
    # Avoid firing procurement post_save a second time: provider usage was already
    # recorded when provider_cost_rub became non-null.
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
def debit_paid(user, amount: Decimal, source_type: str, source_id: str):
    if amount <= 0:
        raise ValidationError("Debit must be positive")
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    key = f"refund:{source_type}:{source_id}"
    existing = LedgerEntry.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    if wallet.paid_rub < amount or wallet.available_rub < amount:
        raise ValidationError("Недостаточно неиспользованного платного баланса для возврата")
    wallet.paid_rub -= amount
    wallet.available_rub -= amount
    wallet.save(update_fields=["paid_rub", "available_rub", "updated_at"])
    return _entry(
        wallet,
        LedgerEntry.Kind.REFUND,
        amount,
        -amount,
        MONEY_ZERO,
        -amount,
        MONEY_ZERO,
        source_type,
        source_id,
        key,
    )


@transaction.atomic
def admin_adjust_balance(
    *, target_user, admin, direction, amount, comment, idempotency_key=""
):
    amount = Decimal(str(amount)).quantize(Decimal("0.0001"))
    comment = str(comment or "").strip()
    idempotency_key = str(idempotency_key or "").strip()
    if amount <= 0:
        raise ValidationError("Сумма корректировки должна быть больше нуля")
    if len(comment) < 3:
        raise ValidationError("Для ручной корректировки обязателен комментарий")
    if direction not in {AdminBalanceAdjustment.Direction.CREDIT, AdminBalanceAdjustment.Direction.DEBIT}:
        raise ValidationError("Неизвестное направление корректировки")
    if not 1 <= len(idempotency_key) <= 120:
        raise ValidationError("Для корректировки обязателен Idempotency-Key")

    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=target_user)
    ledger_key = f"admin-adjustment:{admin.id}:{target_user.id}:{idempotency_key}"
    existing_entry = LedgerEntry.objects.filter(idempotency_key=ledger_key).first()
    if existing_entry is not None:
        existing = AdminBalanceAdjustment.objects.filter(ledger_entry=existing_entry).first()
        if existing is None:
            raise ValidationError("Нарушена связь корректировки с ledger")
        if (
            existing.direction != direction
            or existing.amount_rub != amount
            or existing.comment != comment
        ):
            raise ValidationError("Idempotency-Key уже использован для другой корректировки")
        return existing

    adjustment_id = uuid.uuid4()
    if direction == AdminBalanceAdjustment.Direction.CREDIT:
        # Administrative goodwill/compensation is promo by default and therefore
        # cannot be cashed out through the paid-balance refund flow.
        wallet.available_rub += amount
        wallet.promo_rub += amount
        available_delta = amount
        paid_delta = MONEY_ZERO
        promo_delta = amount
    else:
        if wallet.available_rub < amount:
            raise ValidationError("Недостаточно доступного баланса для ручного списания")
        promo_delta_abs = min(wallet.promo_rub, amount)
        paid_delta_abs = amount - promo_delta_abs
        wallet.available_rub -= amount
        wallet.promo_rub -= promo_delta_abs
        wallet.paid_rub -= paid_delta_abs
        available_delta = -amount
        paid_delta = -paid_delta_abs
        promo_delta = -promo_delta_abs

    wallet.save(update_fields=["available_rub", "paid_rub", "promo_rub", "updated_at"])
    entry = _entry(
        wallet,
        LedgerEntry.Kind.ADJUSTMENT,
        amount,
        available_delta,
        MONEY_ZERO,
        paid_delta,
        promo_delta,
        "admin_adjustment",
        adjustment_id,
        ledger_key,
    )
    return AdminBalanceAdjustment.objects.create(
        id=adjustment_id,
        wallet=wallet,
        admin=admin,
        direction=direction,
        amount_rub=amount,
        comment=comment,
        ledger_entry=entry,
    )
