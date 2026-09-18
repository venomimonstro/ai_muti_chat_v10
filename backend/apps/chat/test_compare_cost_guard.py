from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import CostAnomaly

from .models import CompareRun, CompareVariant, Conversation


@pytest.fixture
def compare_guard_context():
    user = User.objects.create_user(
        username="compare-guard", email="compare-guard@example.test", password="password123"
    )
    conversation = Conversation.objects.create(owner=user, title="Compare guard")
    provider = Provider.objects.create(slug="compare-guard-provider", name="Compare Guard")
    model = AIModel.objects.create(
        provider=provider,
        slug="compare-guard-model",
        display_name="Compare Guard Model",
        upstream_model="compare-guard-model-v1",
        capabilities=["text"],
    )
    run = CompareRun.objects.create(
        owner=user,
        conversation=conversation,
        prompt="test",
        idempotency_key="compare-guard-run",
        state=CompareRun.State.RUNNING,
        model_slugs=[model.slug],
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
    )
    return provider, model, run


@pytest.mark.django_db
def test_compare_variant_over_reserved_maximum_disables_provider(compare_guard_context):
    provider, model, run = compare_guard_context

    variant = CompareVariant.objects.create(
        compare_run=run,
        model=model,
        position=0,
        state=CompareVariant.State.COMPLETED,
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
        actual_cost_rub=Decimal("11"),
        provider_cost_rub=Decimal("8"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert provider.health_state == Provider.HealthState.DISABLED
    anomaly = CostAnomaly.objects.get(dedupe_key=f"compare-critical:{variant.id}")
    assert anomaly.severity == "critical"
    assert anomaly.details["reason"] == "compare_charge_above_reserved_maximum"


@pytest.mark.django_db
def test_compare_variant_guaranteed_loss_disables_provider(compare_guard_context):
    provider, model, run = compare_guard_context

    variant = CompareVariant.objects.create(
        compare_run=run,
        model=model,
        position=0,
        state=CompareVariant.State.COMPLETED,
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
        actual_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("6"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    anomaly = CostAnomaly.objects.get(dedupe_key=f"compare-critical:{variant.id}")
    assert anomaly.details["reason"] == "compare_provider_cost_above_customer_charge"


@pytest.mark.django_db
def test_normal_compare_variant_does_not_trip_provider(compare_guard_context):
    provider, model, run = compare_guard_context

    CompareVariant.objects.create(
        compare_run=run,
        model=model,
        position=0,
        state=CompareVariant.State.COMPLETED,
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
        actual_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("2"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is False
    assert CostAnomaly.objects.filter(provider_slug=provider.slug).count() == 0
