from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import FxRateSnapshot, PriceVersion
from apps.billing.provider_pricing_sync import sync_pricing_catalog


@pytest.mark.django_db
def test_sync_provider_price_converts_native_price_to_rub_and_retires_old_version():
    provider = Provider.objects.create(slug="pricing-provider", name="Pricing Provider")
    AIModel.objects.create(
        provider=provider,
        slug="pricing-model",
        display_name="Pricing Model",
        upstream_model="pricing-model-v1",
    )
    old = PriceVersion.objects.create(
        model_slug="pricing-model",
        input_rub_per_million=Decimal("90.0000"),
        output_rub_per_million=Decimal("180.0000"),
        provider_currency="USD",
        input_price_per_million=Decimal("1.000000"),
        output_price_per_million=Decimal("2.000000"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=Decimal("100.00000000"),
        source="test",
        effective_at=timezone.now(),
    )

    result = sync_pricing_catalog(
        {
            "source": "test-catalog",
            "models": [
                {
                    "model_slug": "pricing-model",
                    "provider_currency": "USD",
                    "input_price_per_million": "1.25",
                    "output_price_per_million": "2.50",
                }
            ],
        },
        apply=True,
    )

    old.refresh_from_db()
    latest = PriceVersion.objects.filter(model_slug="pricing-model", active=True).get()
    assert old.active is False
    assert latest.input_rub_per_million == Decimal("125.0000")
    assert latest.output_rub_per_million == Decimal("250.0000")
    assert result["changes"][0].applied is True


@pytest.mark.django_db
def test_sync_provider_price_refuses_apply_without_required_fx_snapshot():
    provider = Provider.objects.create(slug="pricing-no-fx", name="Pricing No FX")
    AIModel.objects.create(
        provider=provider,
        slug="pricing-no-fx-model",
        display_name="Pricing No FX Model",
        upstream_model="pricing-no-fx-v1",
    )

    with pytest.raises(ValueError, match="Missing FX snapshot USD/RUB"):
        sync_pricing_catalog(
            {
                "models": [
                    {
                        "model_slug": "pricing-no-fx-model",
                        "provider_currency": "USD",
                        "input_price_per_million": "1",
                        "output_price_per_million": "2",
                    }
                ]
            },
            apply=True,
        )
