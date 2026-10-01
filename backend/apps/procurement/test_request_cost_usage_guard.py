from decimal import Decimal
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.billing.models import PriceVersion, RequestCost


def _price(slug: str):
    return PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("1.0000"),
        output_rub_per_million=Decimal("2.0000"),
        provider_currency="RUB",
        input_price_per_million=Decimal("1.000000"),
        output_price_per_million=Decimal("2.000000"),
        markup_percent=Decimal("100.00"),
        active=True,
        effective_from=timezone.now(),
    )


@pytest.mark.django_db(transaction=True)
def test_request_cost_can_switch_model_before_provider_usage():
    primary = _price("guard-primary")
    fallback = _price("guard-fallback")
    cost = RequestCost.objects.create(
        generation_id=uuid.uuid4(),
        price_version=primary,
        estimated_rub=Decimal("1.0000"),
    )

    cost.price_version = fallback
    cost.save(update_fields=["price_version"])
    cost.refresh_from_db()

    assert cost.price_version_id == fallback.id


@pytest.mark.django_db(transaction=True)
def test_stale_request_cost_cannot_switch_model_after_provider_usage_confirmed():
    primary = _price("guard-used-primary")
    fallback = _price("guard-used-fallback")
    cost = RequestCost.objects.create(
        generation_id=uuid.uuid4(),
        price_version=primary,
        estimated_rub=Decimal("1.0000"),
    )

    # Simulate a concurrent provider-delivery checkpoint without refreshing the
    # stale stream-side object held in ``cost``.
    RequestCost.objects.filter(pk=cost.pk).update(
        provider_cost_rub=Decimal("0.2500"),
        input_tokens=128,
        output_tokens=32,
    )

    cost.price_version = fallback
    with pytest.raises(ValidationError, match="подтверждённого расхода"):
        cost.save(update_fields=["price_version"])

    cost.refresh_from_db()
    assert cost.price_version_id == primary.id
    assert cost.provider_cost_rub == Decimal("0.2500")
