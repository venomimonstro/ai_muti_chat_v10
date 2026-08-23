from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BalanceReservation, LedgerEntry, Wallet

MONEY_ZERO = Decimal("0.0000")


def _entry(wallet, kind, amount, available_delta, reserved_delta, source_type, source_id, key):
    return LedgerEntry.objects.create(
        wallet=wallet,
        kind=kind,
        amount_rub=amount,
        available_delta_rub=available_delta,
        reserved_delta_rub=reserved_delta,
        available_after_rub=wallet.available_rub,
        reserved_after_rub=wallet.reserved_rub,
        source_type=source_type,
        source_id=str(source_id),
        idempotency_key=key,
    )


@transaction.atomic
def credit(user, amount: Decimal, source_type: str, source_id: str):
    if amount <= 0:
        raise ValidationError("Credit must be positive")
    wallet, _ = Wallet.objects.select_for_update().get_or_create(user=user)
    key = f"credit:{source_type}:{source_id}"
    existing = LedgerEntry.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    wallet.available_rub += amount
    wallet.save(update_fields=["available_rub", "updated_at"])
    return _entry(
        wallet, LedgerEntry.Kind.CREDIT, amount, amount, MONEY_ZERO, source_type, source_id, key
    )


@transaction.atomic
def reserve(user, amount: Decimal, key: str):
    if amount <= 0:
        raise ValidationError("Reserve must be positive")
    existing = BalanceReservation.objects.filter(idempotency_key=key).first()
    if existing:
        return existing
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.available_rub < amount:
        raise ValidationError("Недостаточно средств")
    wallet.available_rub -= amount
    wallet.reserved_rub += amount
    wallet.save(update_fields=["available_rub", "reserved_rub", "updated_at"])
    reservation = BalanceReservation.objects.create(
        wallet=wallet, amount_rub=amount, idempotency_key=key
    )
    _entry(
        wallet,
        LedgerEntry.Kind.RESERVE,
        amount,
        -amount,
        amount,
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
    wallet.reserved_rub -= reservation.amount_rub
    wallet.available_rub += release_amount
    if wallet.reserved_rub < MONEY_ZERO:
        raise ValidationError("Reserved balance invariant violated")
    wallet.save(update_fields=["available_rub", "reserved_rub", "updated_at"])
    if actual:
        _entry(
            wallet,
            LedgerEntry.Kind.DEBIT,
            actual,
            MONEY_ZERO,
            -actual,
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
            "generation",
            reservation.id,
            f"release:{reservation.id}",
        )
    reservation.actual_rub = actual
    reservation.state = BalanceReservation.State.SETTLED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["actual_rub", "state", "settled_at"])
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
    wallet.save(update_fields=["available_rub", "reserved_rub", "updated_at"])
    _entry(
        wallet,
        LedgerEntry.Kind.RELEASE,
        reservation.amount_rub,
        reservation.amount_rub,
        -reservation.amount_rub,
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
