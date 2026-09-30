from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import PriceVersion
from apps.chat.models import Conversation

from .models import AIModel, Provider, RoutingTierAssignment
from .router import select_route


def _model(provider, slug, *, enabled=True):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=enabled,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    if enabled:
        PriceVersion.objects.create(
            model_slug=slug,
            input_rub_per_million=Decimal("1"),
            output_rub_per_million=Decimal("2"),
            provider_currency="RUB",
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("2"),
            markup_percent=Decimal("100"),
            active=True,
            effective_from=timezone.now(),
        )
    return model


@pytest.mark.django_db
def test_auto_crosses_to_nearest_tier_when_preferred_pool_has_no_ready_model():
    user = User.objects.create_user(
        username="auto-continuity",
        email="auto-continuity@example.test",
        password="password123!",
    )
    provider = Provider.objects.create(
        slug="auto-continuity-echo",
        name="Auto continuity echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        priority=10,
    )
    unavailable_balanced = _model(provider, "auto-balanced-down", enabled=False)
    maximum = _model(provider, "auto-maximum-ready", enabled=True)
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.MEDIUM,
        model=unavailable_balanced,
        priority=10,
        enabled=True,
    )
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.COMPLEX,
        model=maximum,
        priority=10,
        enabled=True,
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="AUTO continuity",
        routing_mode=Conversation.RoutingMode.AUTO,
        selected_model="echo-v1",
    )

    # Ordinary QA classifies to the Medium/balanced tier. That tier has no ready
    # model, so AUTO must continue through Maximum instead of failing the chat.
    route = select_route(conversation=conversation, content="Привет. Ответь коротко.")

    assert route.selected.pk == maximum.pk
    assert route.ordered_models[0].pk == maximum.pk
    assert "резервный уровень" in route.explanation
    assert "Сложный" in route.explanation
    assert conversation.routing_mode == Conversation.RoutingMode.AUTO


@pytest.mark.django_db
def test_explicit_tier_remains_strict_when_its_pool_is_unavailable():
    user = User.objects.create_user(
        username="explicit-tier-strict",
        email="explicit-tier-strict@example.test",
        password="password123!",
    )
    provider = Provider.objects.create(
        slug="explicit-tier-echo",
        name="Explicit tier echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        priority=10,
    )
    unavailable_balanced = _model(provider, "explicit-balanced-down", enabled=False)
    maximum = _model(provider, "explicit-maximum-ready", enabled=True)
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.MEDIUM,
        model=unavailable_balanced,
        priority=10,
        enabled=True,
    )
    RoutingTierAssignment.objects.create(
        tier=RoutingTierAssignment.Tier.COMPLEX,
        model=maximum,
        priority=10,
        enabled=True,
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="Strict medium",
        routing_mode=Conversation.RoutingMode.BALANCED,
        selected_model="echo-v1",
    )

    with pytest.raises(ValidationError, match="нет доступных моделей"):
        select_route(conversation=conversation, content="Привет. Ответь коротко.")
