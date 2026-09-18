from decimal import Decimal

import pytest

from apps.accounts.models import User

from .models import LedgerEntry
from .promotions import grant_signup_promo


@pytest.mark.django_db(transaction=True)
def test_signup_promo_is_idempotent_for_verified_account(settings):
    settings.SIGNUP_PROMO_RUB = Decimal("25.00")
    settings.SIGNUP_PROMO_DAILY_CAP_RUB = Decimal("1000.00")
    user = User.objects.create_user(
        username="promo-once", email="promo-once@example.test", password="password123"
    )

    first = grant_signup_promo(user)
    second = grant_signup_promo(user)

    assert first is not None
    assert second.id == first.id
    assert LedgerEntry.objects.filter(
        source_type="signup_promo",
        source_id=f"signup:{user.id}",
    ).count() == 1
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("25.0000")
    assert user.wallet.promo_rub == Decimal("25.0000")


@pytest.mark.django_db(transaction=True)
def test_global_daily_signup_promo_budget_fails_closed(settings):
    settings.SIGNUP_PROMO_RUB = Decimal("25.00")
    settings.SIGNUP_PROMO_DAILY_CAP_RUB = Decimal("25.00")
    first_user = User.objects.create_user(
        username="promo-first", email="promo-first@example.test", password="password123"
    )
    second_user = User.objects.create_user(
        username="promo-second", email="promo-second@example.test", password="password123"
    )

    assert grant_signup_promo(first_user) is not None
    assert grant_signup_promo(second_user) is None

    assert LedgerEntry.objects.filter(source_type="signup_promo").count() == 1
    assert not hasattr(second_user, "wallet") or second_user.wallet.available_rub == Decimal("0.0000")
