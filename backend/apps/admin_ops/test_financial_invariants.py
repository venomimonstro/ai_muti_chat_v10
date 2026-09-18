from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, Wallet


@pytest.mark.django_db
def test_financial_invariants_accept_consistent_wallet_and_reservation():
    user = User.objects.create_user(
        username="finance-ok",
        email="finance-ok@example.test",
        password="strong-password-123",
    )
    wallet = Wallet.objects.create(
        user=user,
        available_rub=Decimal("7.0000"),
        reserved_rub=Decimal("3.0000"),
        paid_rub=Decimal("5.0000"),
        promo_rub=Decimal("2.0000"),
    )
    BalanceReservation.objects.create(
        wallet=wallet,
        amount_rub=Decimal("3.0000"),
        paid_amount_rub=Decimal("1.0000"),
        promo_amount_rub=Decimal("2.0000"),
        idempotency_key="test-reservation-ok",
    )
    call_command("verify_financial_invariants")


@pytest.mark.django_db
def test_financial_invariants_detect_wallet_reservation_cache_drift():
    user = User.objects.create_user(
        username="finance-bad",
        email="finance-bad@example.test",
        password="strong-password-123",
    )
    wallet = Wallet.objects.create(
        user=user,
        available_rub=Decimal("7.0000"),
        reserved_rub=Decimal("4.0000"),
        paid_rub=Decimal("5.0000"),
        promo_rub=Decimal("2.0000"),
    )
    BalanceReservation.objects.create(
        wallet=wallet,
        amount_rub=Decimal("3.0000"),
        paid_amount_rub=Decimal("1.0000"),
        promo_amount_rub=Decimal("2.0000"),
        idempotency_key="test-reservation-bad",
    )
    with pytest.raises(CommandError, match="Financial invariants failed"):
        call_command("verify_financial_invariants")
