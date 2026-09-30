import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderApiKey, RoutingPolicyVersion
from apps.billing.models import PriceVersion
from apps.billing.services import credit

from .models import Conversation
from .streaming import prepare


def _key(provider, label):
    key = ProviderApiKey(
        provider=provider,
        label=label,
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    )
    key.set_secret(f"{label}-secret")
    key.save()
    return key


def _model(provider, slug):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=32768,
        max_output_tokens=2048,
    )
    version = ModelVersion.objects.create(
        model=model,
        version="v1",
        exact_api_id=slug,
        capabilities=["text", "streaming"],
        context_window=32768,
        max_output_tokens=2048,
        stage=ModelVersion.Stage.ACTIVE,
        activated_at=timezone.now(),
    )
    model.current_version = version
    model.save(update_fields=["current_version"])
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        provider_currency="RUB",
        input_price_per_million=Decimal("10"),
        output_price_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    return model


def _provider(slug, priority):
    provider = Provider.objects.create(
        slug=slug,
        name=slug,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
        priority=priority,
    )
    _key(provider, f"{slug}-key")
    return provider


@pytest.mark.django_db(transaction=True)
def test_prepare_skips_exactly_unfunded_primary_before_any_provider_call(monkeypatch):
    user = User.objects.create_user(username="procurement-fallback", password="password123")
    credit(user, Decimal("10"), "test", "procurement-fallback")
    AIModel.objects.all().update(enabled=False)
    RoutingPolicyVersion.objects.filter(active=True).update(active=False)

    primary_provider = _provider("primary-provider", 10)
    fallback_provider = _provider("fallback-provider", 20)
    primary = _model(primary_provider, "primary-model")
    fallback = _model(fallback_provider, "fallback-model")

    RoutingPolicyVersion.objects.create(
        version="procurement-fallback-test",
        active=True,
        mode_weights={
            "balanced": {"quality": 1.0, "cost": 0.0, "latency": 0.0, "health": 0.0},
        },
        thresholds={
            "default_quality": 0.55,
            "fallback_price_multiplier": 10,
            "tier_models": {"balanced": [primary.slug, fallback.slug]},
        },
    )
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.BALANCED,
        selected_model="echo-v1",
    )

    # Router sees both channels as commercially possible. The exact post-context
    # preflight then detects that the primary can no longer fund this request.
    monkeypatch.setattr(
        "apps.ai_registry.router.quote_has_procurement_capacity",
        lambda provider, price_quote: True,
    )
    monkeypatch.setattr(
        "apps.chat.streaming.quote_has_procurement_capacity",
        lambda provider, price_quote: provider.pk == fallback_provider.pk,
    )

    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Обычный рабочий запрос",
        client_message_id=uuid.uuid4(),
        idempotency_key="exact-procurement-fallback",
    )

    assert created is True
    generation.refresh_from_db()
    decision = generation.routing_decision
    decision.refresh_from_db()
    assert generation.model == fallback.slug
    assert decision.selected_model_id == fallback.id
    assert primary.slug not in generation.route_price_snapshot
    assert fallback.slug in generation.route_price_snapshot
    primary_row = next(item for item in decision.candidate_snapshot if item["model"] == primary.slug)
    assert primary_row["status"] == "rejected"
    assert "procurement_balance_insufficient_exact" in primary_row["reasons"]
