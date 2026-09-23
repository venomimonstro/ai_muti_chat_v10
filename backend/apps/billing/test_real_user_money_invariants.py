from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .models import BalanceReservation, LedgerEntry, Wallet
from .services import credit, debit_paid, reconstruct, reconstruct_buckets, release, reserve, settle


@pytest.fixture
def user(db):
    return User.objects.create_user(username="money-360", email="money-360@example.com", password="password123")


def snapshot(user):
    wallet = Wallet.objects.get(user=user)
    return (
        wallet.available_rub,
        wallet.reserved_rub,
        wallet.paid_rub,
        wallet.promo_rub,
        wallet.entries.count(),
    )


def assert_reconciled(user):
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == wallet.paid_rub + wallet.promo_rub
    assert reconstruct(wallet) == (wallet.available_rub, wallet.reserved_rub)
    assert reconstruct_buckets(wallet) == (wallet.paid_rub, wallet.promo_rub)
    assert wallet.available_rub >= 0
    assert wallet.reserved_rub >= 0
    assert wallet.paid_rub >= 0
    assert wallet.promo_rub >= 0


@pytest.mark.django_db(transaction=True)
def test_payment_credit_is_exactly_once(user):
    first = credit(user, Decimal("500.00"), "payment", "pay-1", bucket="paid")
    second = credit(user, Decimal("500.00"), "payment", "pay-1", bucket="paid")
    assert first.id == second.id
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == Decimal("500.0000")
    assert wallet.paid_rub == Decimal("500.0000")
    assert wallet.entries.filter(kind=LedgerEntry.Kind.CREDIT).count() == 1
    assert_reconciled(user)


@pytest.mark.django_db(transaction=True)
def test_duplicate_generation_reserve_does_not_double_hold(user):
    credit(user, Decimal("500.00"), "payment", "pay-1", bucket="paid")
    first = reserve(user, Decimal("50.00"), "generation:same-request")
    second = reserve(user, Decimal("50.00"), "generation:same-request")
    assert first.id == second.id
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == Decimal("450.0000")
    assert wallet.reserved_rub == Decimal("50.0000")
    assert wallet.entries.filter(kind=LedgerEntry.Kind.RESERVE).count() == 1
    assert_reconciled(user)


@pytest.mark.django_db(transaction=True)
def test_successful_generation_debits_actual_and_releases_unused_reserve_once(user):
    credit(user, Decimal("500.00"), "payment", "pay-1", bucket="paid")
    reservation = reserve(user, Decimal("50.00"), "generation:success")
    settle(reservation.id, Decimal("12.34"))
    after_first = snapshot(user)
    settle(reservation.id, Decimal("12.34"))
    after_second = snapshot(user)
    assert after_first == after_second
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == Decimal("487.6600")
    assert wallet.reserved_rub == Decimal("0.0000")
    assert wallet.paid_rub == Decimal("487.6600")
    reservation.refresh_from_db()
    assert reservation.state == BalanceReservation.State.SETTLED
    assert reservation.actual_rub == Decimal("12.3400")
    assert wallet.entries.filter(kind=LedgerEntry.Kind.DEBIT).count() == 1
    assert wallet.entries.filter(kind=LedgerEntry.Kind.RELEASE).count() == 1
    assert_reconciled(user)


@pytest.mark.django_db(transaction=True)
def test_failed_generation_returns_full_reserve_once(user):
    credit(user, Decimal("500.00"), "payment", "pay-1", bucket="paid")
    reservation = reserve(user, Decimal("50.00"), "generation:failed")
    release(reservation.id)
    after_first = snapshot(user)
    release(reservation.id)
    assert snapshot(user) == after_first
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == Decimal("500.0000")
    assert wallet.reserved_rub == Decimal("0.0000")
    assert wallet.entries.filter(idempotency_key=f"failure-release:{reservation.id}").count() == 1
    assert_reconciled(user)


@pytest.mark.django_db(transaction=True)
def test_insufficient_balance_does_not_mutate_wallet(user):
    credit(user, Decimal("10.00"), "payment", "pay-1", bucket="paid")
    before = snapshot(user)
    with pytest.raises(ValidationError):
        reserve(user, Decimal("20.00"), "generation:too-expensive")
    assert snapshot(user) == before
    assert_reconciled(user)


@pytest.mark.django_db(transaction=True)
def test_promo_is_consumed_before_paid_and_refund_can_only_use_paid_balance(user):
    credit(user, Decimal("100.00"), "payment", "pay-1", bucket="paid")
    credit(user, Decimal("20.00"), "admin_promo", "promo-1", bucket="promo")
    reservation = reserve(user, Decimal("30.00"), "generation:promo-first")
    settle(reservation.id, Decimal("25.00"))
    wallet = Wallet.objects.get(user=user)
    assert wallet.promo_rub == Decimal("0.0000")
    assert wallet.paid_rub == Decimal("95.0000")
    assert wallet.available_rub == Decimal("95.0000")

    first = debit_paid(user, Decimal("40.00"), "refund", "refund-1")
    second = debit_paid(user, Decimal("40.00"), "refund", "refund-1")
    assert first.id == second.id
    wallet.refresh_from_db()
    assert wallet.available_rub == Decimal("55.0000")
    assert wallet.paid_rub == Decimal("55.0000")
    with pytest.raises(ValidationError):
        debit_paid(user, Decimal("60.00"), "refund", "refund-too-large")
    wallet.refresh_from_db()
    assert wallet.available_rub == Decimal("55.0000")
    assert wallet.paid_rub == Decimal("55.0000")
    assert_reconciled(user)
