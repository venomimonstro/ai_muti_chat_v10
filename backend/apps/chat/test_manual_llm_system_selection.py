from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import PriceVersion


@pytest.mark.django_db
def test_customer_can_select_branded_llm_system_model_manually():
    user = User.objects.create_user(
        username="manual-system-user",
        email="manual-system@example.test",
        password="password123",
    )
    provider = Provider.objects.create(
        slug="gigachat",
        name="GigaChat API",
        enabled=True,
        emergency_disabled=False,
        health_state=Provider.HealthState.HEALTHY,
    )
    key = ProviderApiKey(
        provider=provider,
        label="primary",
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    )
    key.set_secret("system-test-key")
    key.save()
    model = AIModel.objects.create(
        provider=provider,
        slug="gigachat-manual-pro",
        display_name="GigaChat Pro",
        upstream_model="GigaChat-2-Pro",
        enabled=True,
        capabilities=["text", "streaming"],
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )

    client = APIClient()
    client.force_authenticate(user)

    catalog = client.get("/api/v1/models/")
    assert catalog.status_code == 200
    row = next(item for item in catalog.data if item["slug"] == model.slug)
    assert row["provider"] == "llm-system"
    assert row["provider_name"] == "LLM System"
    assert row["display_name"] == "LLM System · System Pro"
    assert row["exact_api_id"] == ""
    assert row["available"] is True

    created = client.post(
        "/api/v1/conversations/",
        {
            "title": "Ручной LLM System",
            "routing_mode": "manual",
            "selected_model": model.slug,
        },
        format="json",
    )
    assert created.status_code == 201
    assert created.data["routing_mode"] == "manual"
    assert created.data["selected_model"] == model.slug
