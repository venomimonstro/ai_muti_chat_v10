from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .services import credit, release, reserve


@pytest.mark.django_db
def test_consumer_reservation_velocity_blocks_start_cancel_loop(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "2")
    user = User.objects.create_user(
        username="velocity-user", email="velocity-user@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "velocity")

    first = reserve(user, Decimal("1"), "generation:velocity:1")
    release(first.id)
    second = reserve(user, Decimal("1"), "image:velocity:2")
    release(second.id)

    with pytest.raises(ValidationError, match="Слишком много AI-операций"):
        reserve(user, Decimal("1"), "compare:velocity:3")

    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db
def test_public_api_reservations_use_b2b_limits_not_consumer_velocity(monkeypatch):
    monkeypatch.setenv("CONSUMER_MAX_OPERATIONS_PER_MINUTE", "1")
    user = User.objects.create_user(
        username="velocity-b2b", email="velocity-b2b@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "velocity-b2b")

    first = reserve(user, Decimal("1"), "public-api:one")
    release(first.id)
    second = reserve(user, Decimal("1"), "public-api:two")

    assert second.amount_rub == Decimal("1")
