from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.billing.services import credit, reserve, settle


@pytest.mark.django_db(transaction=True)
def test_single_request_cannot_drain_large_share_of_wallet(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "250")
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_BALANCE_PERCENT", "10")
    user = User.objects.create_user(username="guard-single", email="guard-single@example.test")
    credit(user, Decimal("1000"), "test", "single")

    with pytest.raises(ValidationError, match="Защитный лимит одного AI-запроса"):
        reserve(user, Decimal("101"), "generation:guard-single")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("1000.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_two_answers_cannot_consume_entire_1000_rub_balance(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "250")
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_BALANCE_PERCENT", "10")
    monkeypatch.setenv("CONSUMER_BURST_WINDOW_MINUTES", "10")
    monkeypatch.setenv("CONSUMER_MAX_BURST_SPEND_RUB", "500")
    monkeypatch.setenv("CONSUMER_MAX_BURST_SPEND_PERCENT", "20")
    user = User.objects.create_user(username="guard-two", email="guard-two@example.test")
    credit(user, Decimal("1000"), "test", "two")

    first = reserve(user, Decimal("100"), "generation:first")
    settle(first.id, Decimal("100"))
    second = reserve(user, Decimal("90"), "generation:second")
    settle(second.id, Decimal("90"))

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("810.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")

    # A third expensive request inside the same burst is blocked before provider use.
    with pytest.raises(ValidationError, match="защита от резкого расхода баланса"):
        reserve(user, Decimal("80"), "generation:third")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("810.0000")


@pytest.mark.django_db(transaction=True)
def test_daily_dynamic_cap_limits_damage_from_repeated_requests(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_RUB", "1000")
    monkeypatch.setenv("CONSUMER_MAX_SINGLE_REQUEST_BALANCE_PERCENT", "100")
    monkeypatch.setenv("CONSUMER_MAX_BURST_SPEND_RUB", "10000")
    monkeypatch.setenv("CONSUMER_MAX_BURST_SPEND_PERCENT", "100")
    monkeypatch.setenv("CONSUMER_MAX_DAILY_SPEND_RUB", "20000")
    monkeypatch.setenv("CONSUMER_MAX_DAILY_BALANCE_PERCENT", "50")
    user = User.objects.create_user(username="guard-daily", email="guard-daily@example.test")
    credit(user, Decimal("1000"), "test", "daily")

    first = reserve(user, Decimal("400"), "generation:daily-one")
    settle(first.id, Decimal("400"))

    with pytest.raises(ValidationError, match="дневной защитный лимит"):
        reserve(user, Decimal("101"), "generation:daily-two")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("600.0000")
