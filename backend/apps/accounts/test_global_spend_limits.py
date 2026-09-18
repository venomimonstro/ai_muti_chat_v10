from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.billing.services import credit, reserve


@pytest.mark.django_db
def test_global_single_request_ceiling_blocks_large_reservation(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "100")
    monkeypatch.setenv("CONSUMER_MAX_DAILY_SPEND_RUB", "1000")
    monkeypatch.setenv("CONSUMER_MAX_MONTHLY_SPEND_RUB", "5000")
    user = User.objects.create_user(
        username="single-cap", email="single-cap@example.test", password="password123"
    )
    credit(user, Decimal("1000"), "test", "single-cap")

    with pytest.raises(ValidationError, match="одной операции"):
        reserve(user, Decimal("101"), "single-cap:blocked")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("1000.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db
def test_global_daily_ceiling_counts_active_reservations(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "1000")
    monkeypatch.setenv("CONSUMER_MAX_DAILY_SPEND_RUB", "150")
    monkeypatch.setenv("CONSUMER_MAX_MONTHLY_SPEND_RUB", "5000")
    user = User.objects.create_user(
        username="daily-cap", email="daily-cap@example.test", password="password123"
    )
    credit(user, Decimal("1000"), "test", "daily-cap")
    reserve(user, Decimal("100"), "daily-cap:first")

    with pytest.raises(ValidationError, match="дневной лимит"):
        reserve(user, Decimal("51"), "daily-cap:second")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("900.0000")
    assert user.wallet.reserved_rub == Decimal("100.0000")


@pytest.mark.django_db
def test_user_limit_can_lower_but_not_raise_system_limit(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "1000")
    monkeypatch.setenv("CONSUMER_MAX_DAILY_SPEND_RUB", "200")
    monkeypatch.setenv("CONSUMER_MAX_MONTHLY_SPEND_RUB", "5000")
    user = User.objects.create_user(
        username="user-cap", email="user-cap@example.test", password="password123"
    )
    credit(user, Decimal("1000"), "test", "user-cap")
    preference = user.preferences
    preference.daily_spend_limit_rub = Decimal("500")
    preference.save(update_fields=["daily_spend_limit_rub"])

    with pytest.raises(ValidationError, match="дневной лимит"):
        reserve(user, Decimal("201"), "user-cap:system-block")
