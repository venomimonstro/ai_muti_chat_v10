from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import CostAnomaly

from .models import APIKey, APIUsage, Organization


@pytest.mark.django_db
def test_completed_b2b_usage_with_negative_margin_disables_provider():
    user = User.objects.create_user(
        username="b2b-loss", email="b2b-loss@example.test", password="password123"
    )
    provider = Provider.objects.create(slug="b2b-loss-provider", name="B2B Loss")
    model = AIModel.objects.create(
        provider=provider,
        slug="b2b-loss-model",
        display_name="B2B Loss Model",
        upstream_model="b2b-loss-model-v1",
        capabilities=["text"],
    )
    org = Organization.objects.create(
        name="B2B Loss Org",
        slug="b2b-loss-org",
        billing_user=user,
        monthly_limit_rub=Decimal("1000"),
    )
    key = APIKey.objects.create(
        organization=org,
        created_by=user,
        name="loss-key",
        prefix="ak_b2b_loss",
        secret_hash="0" * 64,
        scopes=["chat.completions"],
        monthly_limit_rub=Decimal("500"),
    )

    usage = APIUsage.objects.create(
        organization=org,
        api_key=key,
        model=model,
        response_id="chatcmpl-loss",
        request_hash="a" * 64,
        state=APIUsage.State.COMPLETED,
        estimated_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("6"),
        charged_rub=Decimal("5"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert provider.health_state == Provider.HealthState.DISABLED
    anomaly = CostAnomaly.objects.get(dedupe_key=f"b2b-critical-loss:{usage.id}")
    assert anomaly.severity == "critical"
    assert anomaly.details["reason"] == "b2b_provider_cost_above_customer_charge"


@pytest.mark.django_db
def test_profitable_b2b_usage_does_not_disable_provider():
    user = User.objects.create_user(
        username="b2b-profit", email="b2b-profit@example.test", password="password123"
    )
    provider = Provider.objects.create(slug="b2b-profit-provider", name="B2B Profit")
    model = AIModel.objects.create(
        provider=provider,
        slug="b2b-profit-model",
        display_name="B2B Profit Model",
        upstream_model="b2b-profit-model-v1",
        capabilities=["text"],
    )
    org = Organization.objects.create(
        name="B2B Profit Org",
        slug="b2b-profit-org",
        billing_user=user,
        monthly_limit_rub=Decimal("1000"),
    )
    key = APIKey.objects.create(
        organization=org,
        created_by=user,
        name="profit-key",
        prefix="ak_b2b_profit",
        secret_hash="0" * 64,
        scopes=["chat.completions"],
        monthly_limit_rub=Decimal("500"),
    )

    APIUsage.objects.create(
        organization=org,
        api_key=key,
        model=model,
        response_id="chatcmpl-profit",
        request_hash="b" * 64,
        state=APIUsage.State.COMPLETED,
        estimated_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("2"),
        charged_rub=Decimal("5"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is False
    assert CostAnomaly.objects.filter(provider_slug=provider.slug).count() == 0
