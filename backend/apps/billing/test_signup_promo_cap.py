from decimal import Decimal

import pytest

from apps.accounts.models import User

from .models import LedgerEntry
from .promotions import grant_signup_promo


@pytest.mark.django_db(transaction=True)
def test_signup_promo_daily_cap_bounds_project_liability(settings):
    settings.SIGNUP_PROMO_RUB = "25.00"
    settings.SIGNUP_PROMO_DAILY_CAP_RUB = "50.00"
    users = [
        User.objects.create_user(
            username=f"promo-{index}",
            email=f"promo-{index}@example.test",
            password="password123",
        )
        for index in range(3)
    ]

    first = grant_signup_promo(users[0])
    second = grant_signup_promo(users[1])
    third = grant_signup_promo(users[2])

    assert first is not None
    assert second is not None
    assert third is None
    users[0].wallet.refresh_from_db()
    users[1].wallet.refresh_from_db()
    assert users[0].wallet.promo_rub == Decimal("25.0000")
    assert users[1].wallet.promo_rub == Decimal("25.0000")
    assert not hasattr(users[2], "wallet") or users[2].wallet.available_rub == Decimal("0.0000")
    total = sum(
        LedgerEntry.objects.filter(source_type="signup_promo").values_list("amount_rub", flat=True),
        Decimal("0"),
    )
    assert total == Decimal("50.0000")


@pytest.mark.django_db(transaction=True)
def test_signup_promo_grant_is_idempotent(settings):
    settings.SIGNUP_PROMO_RUB = "25.00"
    settings.SIGNUP_PROMO_DAILY_CAP_RUB = "1000.00"
    user = User.objects.create_user(
        username="promo-idempotent",
        email="promo-idempotent@example.test",
        password="password123",
    )

    first = grant_signup_promo(user)
    second = grant_signup_promo(user)

    assert first.id == second.id
    assert LedgerEntry.objects.filter(source_type="signup_promo").count() == 1
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("25.0000")
