import pytest

from . import router
from .models import AIModel, Provider, RoutingTierAssignment
from .routing_pools import tier_configuration_present, tier_pool


def _model(slug: str, provider_priority: int = 100):
    provider = Provider.objects.create(
        slug=f"{slug}-provider",
        name=slug,
        priority=provider_priority,
        enabled=True,
    )
    return AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=True,
    )


@pytest.mark.django_db
def test_database_tier_assignments_override_legacy_json():
    legacy = _model("legacy")
    selected = _model("selected")
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.MEDIUM,
        model=selected,
        priority=10,
        enabled=True,
    )

    thresholds = {"tier_models": {"balanced": [legacy.slug]}}
    assert tier_configuration_present(thresholds) is True
    assert tier_pool(thresholds, "balanced") == [selected.slug]
    assert router._tier_pool(thresholds, "balanced") == [selected.slug]


@pytest.mark.django_db
def test_database_pool_order_is_admin_priority_then_provider_priority():
    later = _model("later", provider_priority=1)
    first = _model("first", provider_priority=500)
    second = _model("second", provider_priority=1)
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.SIMPLE,
        model=later,
        priority=20,
    )
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.SIMPLE,
        model=second,
        priority=10,
    )
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.SIMPLE,
        model=first,
        priority=10,
    )

    assert tier_pool({}, "economy") == [second.slug, first.slug, later.slug]


@pytest.mark.django_db
def test_once_database_pools_are_used_unassigned_tier_is_empty_fail_closed():
    model = _model("only-simple")
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.SIMPLE,
        model=model,
        enabled=True,
    )
    thresholds = {"tier_models": {"maximum": [model.slug]}}

    assert tier_configuration_present(thresholds) is True
    assert tier_pool(thresholds, "maximum") == []
