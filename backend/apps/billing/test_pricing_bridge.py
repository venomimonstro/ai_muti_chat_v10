from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider

from . import pricing
from .models import PriceVersion


def _model(slug: str, input_cost="1.2500", output_cost="5.5000"):
    provider = Provider.objects.create(slug=f"{slug}-provider", name=slug)
    return AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        input_price_rub_per_million=Decimal(input_cost),
        output_price_rub_per_million=Decimal(output_cost),
        enabled=True,
    )


@pytest.mark.django_db
def test_active_price_is_created_once_from_configured_model_cost():
    model = _model("bridge-model")

    first = pricing.active_price(model.slug)
    second = pricing.active_price(model.slug)

    assert first.pk == second.pk
    assert PriceVersion.objects.filter(model_slug=model.slug, active=True).count() == 1
    assert first.provider_currency == "RUB"
    assert first.input_price_per_million == Decimal("1.250000")
    assert first.output_price_per_million == Decimal("5.500000")


@pytest.mark.django_db
def test_missing_model_cost_remains_fail_closed():
    model = _model("bridge-empty", input_cost="0", output_cost="0")

    with pytest.raises(ValidationError):
        pricing.active_price(model.slug)

    assert not PriceVersion.objects.filter(model_slug=model.slug).exists()


@pytest.mark.django_db
def test_existing_active_price_is_never_replaced_by_legacy_model_cost():
    model = _model("bridge-existing", input_cost="99", output_cost="99")
    existing = PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("2"),
        output_rub_per_million=Decimal("6"),
        provider_currency="RUB",
        input_price_per_million=Decimal("2"),
        output_price_per_million=Decimal("6"),
        markup_percent=Decimal("80"),
        active=True,
        effective_from=timezone.now(),
    )

    resolved = pricing.active_price(model.slug)

    assert resolved.pk == existing.pk
    assert PriceVersion.objects.filter(model_slug=model.slug, active=True).count() == 1
    assert resolved.markup_percent == Decimal("80.00")
