from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount

from . import pricing
from .models import FxRateSnapshot, PriceVersion


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


def _funded_currency(model, currency: str):
    key = ProviderApiKey(provider=model.provider, label=f"{currency}-key")
    key.set_secret(f"{currency.lower()}-secret")
    key.save()
    return ProviderFundingAccount.objects.create(
        provider=model.provider,
        api_key=key,
        label=f"{currency} account",
        currency=currency,
        is_default=True,
        active=True,
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
def test_bridge_converts_rub_legacy_cost_to_default_funding_currency():
    model = _model("bridge-usd", input_cost="100", output_cost="500")
    _funded_currency(model, "USD")
    FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=Decimal("100"),
        source="test",
        effective_at=timezone.now(),
    )

    price = pricing.active_price(model.slug)
    quote = pricing.quote(
        price,
        1_000_000,
        1_000_000,
        provider_slug=model.provider.slug,
        model_slug=model.slug,
    )

    assert price.provider_currency == "USD"
    assert price.input_rub_per_million == Decimal("100.0000")
    assert price.output_rub_per_million == Decimal("500.0000")
    assert price.input_price_per_million == Decimal("1.000000")
    assert price.output_price_per_million == Decimal("5.000000")
    assert quote.pricing_snapshot["provider_currency"] == "USD"
    assert Decimal(quote.pricing_snapshot["fx_rate"]) == Decimal("100.00000000")
    assert quote.provider_cost_rub == Decimal("600.0000")


@pytest.mark.django_db
def test_bridge_fails_closed_when_funding_currency_fx_is_missing():
    model = _model("bridge-eur", input_cost="100", output_cost="500")
    _funded_currency(model, "EUR")

    with pytest.raises(ValidationError, match="FX snapshot EUR/RUB"):
        pricing.active_price(model.slug)

    assert not PriceVersion.objects.filter(model_slug=model.slug).exists()


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
