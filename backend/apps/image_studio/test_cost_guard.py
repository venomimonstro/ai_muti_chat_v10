from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .models import ImageGeneration, ImageModel


@pytest.mark.django_db
def test_completed_image_with_negative_margin_disables_provider():
    user = User.objects.create_user(
        username="image-loss", email="image-loss@example.test", password="password123"
    )
    provider = Provider.objects.create(slug="image-loss-provider", name="Image Loss")
    model = ImageModel.objects.create(
        provider=provider,
        slug="image-loss-model",
        display_name="Image Loss Model",
        upstream_model="image-loss-v1",
        provider_price_per_image=Decimal("6"),
    )

    generation = ImageGeneration.objects.create(
        owner=user,
        model=model,
        prompt="test",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        actual_count=1,
        state=ImageGeneration.State.COMPLETED,
        idempotency_key="image-loss-test",
        estimated_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("6"),
        actual_cost_rub=Decimal("5"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert provider.health_state == Provider.HealthState.DISABLED
    anomaly = CostAnomaly.objects.get(dedupe_key=f"image-critical-loss:{generation.id}")
    assert anomaly.severity == "critical"
    assert anomaly.details["reason"] == "image_provider_cost_above_customer_charge"


@pytest.mark.django_db
def test_profitable_image_does_not_disable_provider():
    user = User.objects.create_user(
        username="image-profit", email="image-profit@example.test", password="password123"
    )
    provider = Provider.objects.create(slug="image-profit-provider", name="Image Profit")
    model = ImageModel.objects.create(
        provider=provider,
        slug="image-profit-model",
        display_name="Image Profit Model",
        upstream_model="image-profit-v1",
        provider_price_per_image=Decimal("2"),
    )

    ImageGeneration.objects.create(
        owner=user,
        model=model,
        prompt="test",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        actual_count=1,
        state=ImageGeneration.State.COMPLETED,
        idempotency_key="image-profit-test",
        estimated_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("2"),
        actual_cost_rub=Decimal("5"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is False
    assert CostAnomaly.objects.filter(provider_slug=provider.slug).count() == 0
