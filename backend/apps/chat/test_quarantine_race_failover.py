import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.model_quarantine import quarantine_model
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from .models import Conversation, Generation, GenerationAttempt
from .streaming import prepare, run


@pytest.mark.django_db(transaction=True)
def test_model_quarantined_after_prepare_falls_back_without_provider_outage():
    user = User.objects.create_user(
        username="quarantine-race-user",
        email="quarantine-race@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "quarantine-race")
    provider = Provider.objects.create(
        slug="quarantine-race-echo",
        name="Quarantine race Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
        priority=1,
    )
    fallback = AIModel.objects.create(
        provider=provider,
        slug="quarantine-race-fallback",
        display_name="Quarantine race fallback",
        upstream_model="quarantine-race-fallback",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=1024,
    )
    primary = AIModel.objects.create(
        provider=provider,
        slug="quarantine-race-primary",
        display_name="Quarantine race primary",
        upstream_model="quarantine-race-primary",
        enabled=True,
        capabilities=["text", "streaming"],
        fallback_model=fallback,
        context_window=8192,
        max_output_tokens=1024,
    )
    for model in (primary, fallback):
        PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=Decimal("10"),
            output_rub_per_million=Decimal("20"),
            markup_percent=Decimal("100"),
            effective_from=timezone.now(),
        )
    conversation = Conversation.objects.create(
        owner=user,
        selected_model=primary.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )

    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Проверь безопасный fallback",
        client_message_id=uuid.uuid4(),
        idempotency_key="quarantine-race:request",
    )
    assert created is True
    assert generation.model == primary.slug

    quarantine_model(
        primary,
        ProviderError("removed after prepare", code="model_not_found", retryable=False),
    )
    body = "".join(run(generation))

    generation.refresh_from_db()
    provider.refresh_from_db()
    user.wallet.refresh_from_db()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    request_cost = RequestCost.objects.get(generation_id=generation.id)
    attempts = list(generation.attempts.order_by("sequence"))

    assert "event: completed" in body
    assert generation.state == Generation.State.COMPLETED
    assert generation.routed_model == fallback.slug
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert len(attempts) == 2
    assert attempts[0].model_slug == primary.slug
    assert attempts[0].state == GenerationAttempt.State.FAILED
    assert attempts[0].error_code == "model_not_found"
    assert attempts[1].model_slug == fallback.slug
    assert attempts[1].state == GenerationAttempt.State.COMPLETED
    assert reservation.state == BalanceReservation.State.SETTLED
    assert request_cost.charged_rub == generation.actual_cost_rub
    assert user.wallet.reserved_rub == Decimal("0.0000")
