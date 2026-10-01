import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit, release, reserve

from . import provider_delivery_checkpoint


@pytest.mark.django_db(transaction=True)
def test_provider_delivery_checkpoint_prevents_false_full_refund():
    user = User.objects.create_user(
        username="delivery-checkpoint-user",
        email="delivery-checkpoint@example.test",
        password="password123!",
    )
    credit(user, Decimal("100"), "test", "delivery-checkpoint")
    generation_id = uuid.uuid4()
    reservation = reserve(user, Decimal("5"), f"generation:{generation_id}")
    price = PriceVersion.objects.create(
        model_slug="delivery-checkpoint-model",
        input_rub_per_million=Decimal("1000000"),
        output_rub_per_million=Decimal("1000000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=price,
        estimated_rub=Decimal("5"),
    )
    model = SimpleNamespace(slug=price.model_slug)
    completed = SimpleNamespace(input_tokens=10, output_tokens=10)

    token = provider_delivery_checkpoint._CURRENT_GENERATION_ID.set(generation_id)
    try:
        provider_delivery_checkpoint._checkpoint(model, completed)
    finally:
        provider_delivery_checkpoint._CURRENT_GENERATION_ID.reset(token)

    request_cost.refresh_from_db()
    assert request_cost.provider_cost_rub == Decimal("20.0000")
    assert request_cost.input_tokens == 10
    assert request_cost.output_tokens == 10

    # Simulate the ordinary customer-settlement path failing after provider delivery.
    # The generic failure handler calls release(); durable provider usage must make
    # release settle at the already authorized ceiling rather than refunding 100%.
    closed = release(reservation.id)
    closed.refresh_from_db()
    request_cost.refresh_from_db()
    user.wallet.refresh_from_db()

    assert closed.state == BalanceReservation.State.SETTLED
    assert closed.actual_rub == Decimal("5.0000")
    assert request_cost.charged_rub == Decimal("5.0000")
    assert user.wallet.available_rub == Decimal("95.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
