from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .services import credit, release, reserve


@pytest.mark.django_db(transaction=True)
def test_consumer_operation_velocity_blocks_repeated_provider_start_abuse(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "2")
    user = User.objects.create_user(
        username="velocity", email="velocity@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "velocity")

    first = reserve(user, Decimal("1"), "generation:first")
    release(first.id)
    second = reserve(user, Decimal("1"), "image:second")
    release(second.id)

    with pytest.raises(ValidationError, match="Слишком много AI-операций"):
        reserve(user, Decimal("1"), "compare:third")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_idempotent_retry_does_not_consume_another_velocity_slot(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "1")
    user = User.objects.create_user(
        username="velocity-retry", email="velocity-retry@example.test", password="password123"
    )
    credit(user, Decimal("10"), "test", "velocity-retry")

    first = reserve(user, Decimal("1"), "generation:same")
    repeated = reserve(user, Decimal("1"), "generation:same")

    assert repeated.id == first.id


@pytest.mark.django_db(transaction=True)
def test_public_api_uses_its_own_rpm_and_is_not_limited_by_consumer_velocity(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "1")
    user = User.objects.create_user(
        username="velocity-api", email="velocity-api@example.test", password="password123"
    )
    credit(user, Decimal("10"), "test", "velocity-api")

    first = reserve(user, Decimal("1"), "public-api:first")
    second = reserve(user, Decimal("1"), "public-api:second")

    assert first.id != second.id


@pytest.mark.django_db(transaction=True)
def test_public_api_is_not_capped_by_consumer_daily_limit_but_still_requires_prefunded_wallet(
    monkeypatch,
):
    monkeypatch.setenv("CONSUMER_MAX_DAILY_SPEND_RUB", "1")
    monkeypatch.setenv("CONSUMER_MAX_MONTHLY_SPEND_RUB", "1")
    user = User.objects.create_user(
        username="b2b-budget-separation",
        email="b2b-budget-separation@example.test",
        password="password123",
    )
    credit(user, Decimal("10"), "test", "b2b-budget-separation")

    reservation = reserve(user, Decimal("2"), "public-api:budgeted")
    assert reservation.amount_rub == Decimal("2.0000")

    with pytest.raises(ValidationError, match="Недостаточно средств"):
        reserve(user, Decimal("20"), "public-api:too-expensive")
