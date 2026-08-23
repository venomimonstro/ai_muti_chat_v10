from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .models import LedgerEntry
from .services import credit, reconstruct, reserve, settle


@pytest.mark.django_db(transaction=True)
def test_ledger_is_idempotent_and_immutable():
    user = User.objects.create_user(username="olga", email="o@example.com", password="password123")
    first = credit(user, Decimal("10"), "test", "one")
    second = credit(user, Decimal("10"), "test", "one")
    assert first.id == second.id
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("10")
    first.amount_rub = Decimal("999")
    with pytest.raises(ValidationError):
        first.save()


@pytest.mark.django_db(transaction=True)
def test_reservation_settlement_cannot_double_debit():
    user = User.objects.create_user(username="user", email="u@example.com", password="password123")
    credit(user, Decimal("10"), "test", "two")
    reservation = reserve(user, Decimal("2"), "request:1")
    settle(reservation.id, Decimal("0.5"))
    settle(reservation.id, Decimal("0.5"))
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("9.5")
    assert user.wallet.reserved_rub == Decimal("0")
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.DEBIT).count() == 1
    assert reconstruct(user.wallet) == (Decimal("9.5000"), Decimal("0.0000"))


@pytest.mark.django_db(transaction=True)
def test_negative_balance_is_rejected():
    user = User.objects.create_user(username="empty", email="e@example.com", password="password123")
    credit(user, Decimal("1"), "test", "three")
    with pytest.raises(ValidationError):
        reserve(user, Decimal("2"), "request:2")
