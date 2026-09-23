from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import PriceVersion
from apps.chat.models import Conversation

from .adapters import adapter_for
from .gigachat_adapter import GigaChatAPIAdapter
from .models import AIModel, Provider, ProviderApiKey, RoutingPolicyVersion
from .router import DEFAULT_WEIGHTS, select_route


def _priced_model(slug, provider, *, price=10, latency=100):
    provider.health_state = Provider.HealthState.HEALTHY
    provider.last_latency_ms = latency
    provider.enabled = True
    provider.emergency_disabled = False
    provider.save(update_fields=["health_state", "last_latency_ms", "enabled", "emergency_disabled"])
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=32768,
        max_output_tokens=4096,
    )
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal(str(price)),
        output_rub_per_million=Decimal(str(price)),
        markup_percent=100,
        active=True,
        effective_from=timezone.now(),
    )
    return model


def _activate_policy(version, thresholds):
    RoutingPolicyVersion.objects.filter(active=True).update(active=False)
    return RoutingPolicyVersion.objects.create(
        version=version,
        active=True,
        mode_weights=DEFAULT_WEIGHTS,
        thresholds=thresholds,
    )


@pytest.mark.django_db
def test_gigachat_adapter_is_external_api_adapter_only():
    provider, _ = Provider.objects.get_or_create(
        slug="gigachat",
        defaults={"name": "GigaChat API"},
    )
    provider.name = "GigaChat API"
    provider.adapter_type = Provider.AdapterType.DEEPSEEK_CHAT
    provider.api_base_url = "https://api.giga.chat/v1"
    provider.enabled = True
    provider.emergency_disabled = False
    provider.health_state = Provider.HealthState.HEALTHY
    provider.save()
    provider.api_keys.all().delete()
    key = ProviderApiKey(provider=provider, label="test", enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY)
    key.set_secret("authorization-key")
    key.save()
    model = AIModel.objects.create(
        provider=provider,
        slug="gigachat-api-test",
        display_name="GigaChat API Test",
        upstream_model="GigaChat-2-Max",
        enabled=True,
    )

    adapter = adapter_for(model)

    assert isinstance(adapter, GigaChatAPIAdapter)
    assert adapter.base_url == "https://api.giga.chat/v1"
    assert "llama" not in adapter.__class__.__module__.lower()
    assert "local" not in adapter.__class__.__name__.lower()


@pytest.mark.django_db
def test_gigachat_adapter_prefers_persisted_scope():
    provider, _ = Provider.objects.get_or_create(slug="gigachat", defaults={"name": "GigaChat API"})
    provider.auth_config = {"scope": "GIGACHAT_API_B2B"}
    provider.save(update_fields=["auth_config"])

    adapter = GigaChatAPIAdapter(
        authorization_key="authorization-key",
        base_url="https://api.giga.chat/v1",
        scope="GIGACHAT_API_PERS",
    )

    assert adapter.scope == "GIGACHAT_API_B2B"


@pytest.mark.django_db
def test_admin_pinned_medium_model_is_selected_before_higher_score_candidate():
    user = User.objects.create_user(username="tier-user", email="tier@example.com", password="password123")
    pinned_provider = Provider.objects.create(slug="pinned-api", name="Pinned API")
    other_provider = Provider.objects.create(slug="other-api", name="Other API")
    pinned = _priced_model("pinned-medium", pinned_provider, price=20, latency=500)
    _priced_model("other-strong", other_provider, price=5, latency=10)
    _activate_policy(
        "tier-test-v1",
        {
            "default_quality": 0.55,
            "economy_min_quality": 0.60,
            "fallback_price_multiplier": 10,
            "unknown_latency_ms": 1500,
            "tier_models": {"balanced": pinned.slug},
        },
    )
    conversation = Conversation.objects.create(owner=user, routing_mode=Conversation.RoutingMode.BALANCED)

    route = select_route(conversation=conversation, content="Обычный рабочий вопрос")

    assert route.selected.slug == pinned.slug
    assert route.ordered_models[0].slug == pinned.slug
    assert "закреплён администратором" in route.explanation


@pytest.mark.django_db
def test_unavailable_pinned_model_falls_back_to_another_external_api():
    user = User.objects.create_user(username="tier-fallback", email="tier-fallback@example.com", password="password123")
    pinned_provider = Provider.objects.create(slug="down-api", name="Down API")
    fallback_provider = Provider.objects.create(slug="fallback-api", name="Fallback API")
    pinned = _priced_model("down-medium", pinned_provider, price=10, latency=50)
    fallback = _priced_model("fallback-medium", fallback_provider, price=10, latency=60)
    pinned_provider.emergency_disabled = True
    pinned_provider.save(update_fields=["emergency_disabled"])
    _activate_policy(
        "tier-fallback-v1",
        {
            "default_quality": 0.55,
            "economy_min_quality": 0.60,
            "fallback_price_multiplier": 2,
            "unknown_latency_ms": 1500,
            "tier_models": {"balanced": pinned.slug},
        },
    )
    conversation = Conversation.objects.create(owner=user, routing_mode=Conversation.RoutingMode.BALANCED)

    route = select_route(conversation=conversation, content="Обычный рабочий вопрос")

    assert route.selected.slug == fallback.slug
    assert route.selected.provider.slug == "fallback-api"
    rejected = next(item for item in route.candidates if item["model"] == pinned.slug)
    assert "provider_unavailable" in rejected["reasons"]
