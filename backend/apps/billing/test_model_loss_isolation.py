import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider

from .models import CostAnomaly, PriceVersion, RequestCost
from .reconciliation import record_cost_outcome


@pytest.mark.django_db
def test_negative_margin_disables_only_losmaking_model_not_provider():
    provider = Provider.objects.create(
        slug="loss-isolation-echo",
        name="Loss Isolation Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        emergency_disabled=False,
        health_state=Provider.HealthState.HEALTHY,
    )
    broken = AIModel.objects.create(
        provider=provider,
        slug="loss-isolation-broken",
        display_name="Loss isolation broken",
        upstream_model="loss-isolation-broken",
        enabled=True,
    )
    sibling = AIModel.objects.create(
        provider=provider,
        slug="loss-isolation-sibling",
        display_name="Loss isolation sibling",
        upstream_model="loss-isolation-sibling",
        enabled=True,
    )
    price = PriceVersion.objects.create(
        model_slug=broken.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=uuid.uuid4(),
        price_version=price,
        estimated_rub=Decimal("5"),
        expected_provider_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("10"),
        charged_rub=Decimal("5"),
        input_tokens=10,
        output_tokens=10,
        gross_profit_rub=Decimal("-5"),
        gross_margin_percent=Decimal("-100"),
    )

    record_cost_outcome(request_cost, model=broken)

    broken.refresh_from_db()
    sibling.refresh_from_db()
    provider.refresh_from_db()
    request_cost.refresh_from_db()
    assert broken.enabled is False
    assert sibling.enabled is True
    assert provider.enabled is True
    assert provider.emergency_disabled is False
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert request_cost.reconciliation_status == RequestCost.ReconciliationStatus.UNDERCHARGED
    anomaly = CostAnomaly.objects.get(
        dedupe_key=f"critical-loss:{request_cost.id}"
    )
    assert anomaly.severity == "critical"
    assert anomaly.model_slug == broken.slug
    assert anomaly.provider_slug == provider.slug
    assert anomaly.details["scope"] == "model_economics"
