from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion


@pytest.mark.django_db
def test_admin_creates_new_price_version_without_mutating_history(settings):
    settings.ADMIN_MFA_ENFORCED = False
    admin = User.objects.create_user(
        username="pricing-admin",
        email="pricing-admin@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    provider = Provider.objects.create(slug="pricing-provider", name="Pricing Provider")
    model = AIModel.objects.create(
        provider=provider,
        slug="pricing-model",
        display_name="Pricing Model",
        upstream_model="pricing-v1",
    )
    client = APIClient()
    client.force_authenticate(admin)

    first = client.post(
        "/api/v1/admin/pricing/",
        {
            "model": model.slug,
            "input_rub_per_million": "10.25",
            "output_rub_per_million": "30.75",
            "markup_percent": "100",
        },
        format="json",
    )
    assert first.status_code == 201
    first_row = PriceVersion.objects.get(pk=first.json()["id"])
    assert first_row.input_rub_per_million == Decimal("10.2500")

    second = client.post(
        "/api/v1/admin/pricing/",
        {
            "model": model.slug,
            "input_rub_per_million": "12.00",
            "output_rub_per_million": "36.00",
            "markup_percent": "110",
        },
        format="json",
    )
    assert second.status_code == 201
    assert PriceVersion.objects.filter(model_slug=model.slug).count() == 2
    first_row.refresh_from_db()
    assert first_row.input_rub_per_million == Decimal("10.2500")
    assert first_row.output_rub_per_million == Decimal("30.7500")
