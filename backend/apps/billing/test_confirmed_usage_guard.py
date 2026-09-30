import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User

from .models import BalanceReservation, PriceVersion, RequestCost
from .services import credit, release, reserve


@pytest.mark.django_db(transaction=True)
def test_confirmed_usage_above_reserve_settles_at_authorized_maximum():
    user = User.objects.create_user(
        username="confirmed-usage-guard",
        email="confirmed-usage-guard@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "confirmed-usage-guard")
    generation_id = uuid.uuid4()
    reservation = reserve(
        user,
        Decimal("5"),
        f"generation:{generation_id}",
    )
    price = PriceVersion.objects.create(
        model_slug="confirmed-usage-expensive-model",
        input_rub_per_million=Decimal("1000000"),
        output_rub_per_million=Decimal("1000000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=price,
        estimated_rub=Decimal("5"),
        provider_cost_rub=Decimal("20"),
        input_tokens=10,
        output_tokens=10,
    )

    closed = release(reservation.id)

    closed.refresh_from_db()
    request_cost.refresh_from_db()
    user.wallet.refresh_from_db()
    assert closed.state == BalanceReservation.State.SETTLED
    assert closed.actual_rub == Decimal("5.0000")
    assert request_cost.charged_rub == Decimal("5.0000")
    assert user.wallet.available_rub == Decimal("95.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
