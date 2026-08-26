from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import enforce_spend_limits, notify_low_balance

from .models import BalanceReservation, LedgerEntry, Wallet

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
    existing = BalanceReservation.objects.select_for_update().filter(idempotency_key=key).first()
    if existing:
        if existing.state == BalanceReservation.State.ACTIVE:
            return existing
        if existing.state == BalanceReservation.State.SETTLED:
            return existing
        if existing.state != BalanceReservation.State.RELEASED:
            raise ValidationError("Reservation is in an unexpected state")
        wallet = Wallet.objects.select_for_update().get(pk=existing.wallet_id)
        if wallet.user_id != user.id:
            raise ValidationError("Reservation belongs to another wallet")
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
        existing.amount_rub = amount
        existing.paid_amount_rub = paid_amount
        existing.promo_amount_rub = promo_amount
        existing.actual_rub = None
        existing.state = BalanceReservation.State.ACTIVE
        existing.settled_at = None
        existing.save(
            update_fields=[
                "amount_rub",
                "paid_amount_rub",
                "promo_amount_rub",
                "actual_rub",
                "state",
                "settled_at",
            ]
        )
        attempt = LedgerEntry.objects.filter(
            source_id=str(existing.id),
            kind=LedgerEntry.Kind.RESERVE,
        ).count()
        _entry(
            wallet,
            LedgerEntry.Kind.RESERVE,
            amount,
            -amount,
            amount,
            -paid_amount,
            -promo_amount,
            "generation",
            existing.id,
            f"reserve:{existing.id}:{attempt}",
        )
        return existing
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
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
    """Rebuild cached wallet buckets only from immutable ledger deltas."""
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
