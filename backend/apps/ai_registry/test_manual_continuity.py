from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import PriceVersion
from apps.chat.models import Conversation

from .models import AIModel, Provider
from .router import select_route


def _priced_model(provider, slug, *, enabled=True, input_price="1", output_price="2"):
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
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal(input_price),
        output_rub_per_million=Decimal(output_price),
        provider_currency="RUB",
        input_price_per_million=Decimal(input_price),
        output_price_per_million=Decimal(output_price),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    return model


@pytest.mark.django_db
def test_disabled_manual_model_falls_back_to_ready_model_instead_of_breaking_chat():
    user = User.objects.create_user(
        username="manual-continuity",
        email="manual-continuity@example.test",
        password="password123!",
    )
    provider = Provider.objects.create(
        slug="manual-continuity-echo",
        name="Manual continuity echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        priority=10,
    )
    disabled = _priced_model(provider, "manual-disabled", enabled=False)
    ready = _priced_model(provider, "manual-ready", enabled=True)
    conversation = Conversation.objects.create(
        owner=user,
        title="Manual stale route",
        routing_mode=Conversation.RoutingMode.MANUAL,
        selected_model=disabled.slug,
    )

    route = select_route(conversation=conversation, content="Ответь коротко")

    assert route.selected.pk == ready.pk
    assert route.ordered_models[0].pk == ready.pk
    assert "отключена или недоступна" in route.explanation
    assert ready.slug in route.explanation


@pytest.mark.django_db
def test_missing_manual_model_slug_also_uses_continuity_route():
    user = User.objects.create_user(
        username="manual-continuity-missing",
        email="manual-continuity-missing@example.test",
        password="password123!",
    )
    provider = Provider.objects.create(
        slug="manual-continuity-missing-echo",
        name="Manual continuity missing echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        priority=10,
    )
    ready = _priced_model(provider, "manual-ready-missing", enabled=True)
    conversation = Conversation.objects.create(
        owner=user,
        title="Manual missing route",
        routing_mode=Conversation.RoutingMode.MANUAL,
        selected_model="provider-model-that-no-longer-exists",
    )

    route = select_route(conversation=conversation, content="Ответь коротко")

    assert route.selected.pk == ready.pk
    assert route.ordered_models[0].pk == ready.pk
    assert "provider-model-that-no-longer-exists" in route.explanation


@pytest.mark.django_db
def test_unavailable_enabled_manual_primary_does_not_cap_first_working_fallback_by_old_price():
    user = User.objects.create_user(
        username="manual-continuity-expensive",
        email="manual-continuity-expensive@example.test",
        password="password123!",
    )
    unavailable_provider = Provider.objects.create(
        slug="manual-unavailable-echo",
        name="Unavailable manual provider",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        emergency_disabled=True,
        priority=1,
    )
    ready_provider = Provider.objects.create(
        slug="manual-expensive-ready-echo",
        name="Ready expensive provider",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
        priority=20,
    )
    primary = _priced_model(
        unavailable_provider,
        "manual-cheap-unavailable",
        input_price="1",
        output_price="1",
    )
    fallback = _priced_model(
        ready_provider,
        "manual-expensive-ready",
        input_price="100",
        output_price="100",
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="Manual unavailable route",
        routing_mode=Conversation.RoutingMode.MANUAL,
        selected_model=primary.slug,
    )

    route = select_route(conversation=conversation, content="Ответь коротко")

    assert route.selected.pk == fallback.pk
    assert route.ordered_models[0].pk == fallback.pk
    assert "автоматически направлен" in route.explanation
