from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, ModelVersion, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit

from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_funded_user_can_use_economy_preview_stream_and_settlement():
    user = User.objects.create_user(
        username="economy-funded-user",
        email="economy-funded@example.test",
        password="test-password-123",
    )
    credit(user, Decimal("50.00"), "test", "economy-funded")

    provider = Provider.objects.create(
        slug="economy-echo",
        name="Economy Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
        last_latency_ms=5,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="economy-echo-v1",
        display_name="Economy Echo",
        upstream_model="economy-echo-v1",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    version = ModelVersion.objects.create(
        model=model,
        version="economy-echo-v1-active",
        exact_api_id="economy-echo-v1",
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
        stage=ModelVersion.Stage.ACTIVE,
        activated_at=timezone.now(),
    )
    model.current_version = version
    model.save(update_fields=["current_version"])

    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10.00"),
        output_rub_per_million=Decimal("20.00"),
        provider_currency="RUB",
        markup_percent=Decimal("50.00"),
        active=True,
        effective_from=timezone.now(),
    )

    conversation = Conversation.objects.create(
        owner=user,
        title="AUTO economy",
        # Deliberately stale/manual-looking selected_model: AUTO must ignore it.
        selected_model="echo-v1",
        routing_mode=Conversation.RoutingMode.ECONOMY,
    )
    client = APIClient()
    client.force_authenticate(user)

    settings_response = client.patch(
        f"/api/v1/conversation-settings/{conversation.id}/",
        {"routing_mode": Conversation.RoutingMode.ECONOMY},
        format="json",
    )
    assert settings_response.status_code == 200
    assert settings_response.data["routing_mode"] == Conversation.RoutingMode.ECONOMY

    payload = {
        "content": "Привет. Ответь коротко.",
        "client_message_id": "0cc9b3c8-7119-4af4-aeb0-6fa807bdd31f",
    }
    preview = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/preview/",
        payload,
        format="json",
    )
    assert preview.status_code == 200
    assert preview.data["selected_model"] == model.slug
    assert Decimal(preview.data["estimated_max_rub"]) > 0
    assert preview.data["blocked_by_spend_guard"] is False

    before = user.wallet.available_rub
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="economy-funded:first",
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: routing" in body
    assert "event: delta" in body
    assert "event: completed" in body
    assert "Тестовый ответ:" in body

    generation = Generation.objects.get(owner=user)
    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()

    assert generation.state == Generation.State.COMPLETED
    assert generation.routed_model == model.slug
    assert generation.assistant_message.status == Message.Status.COMPLETED
    assert generation.actual_cost_rub is not None
    assert generation.actual_cost_rub > 0
    assert user.wallet.available_rub < before
    assert user.wallet.reserved_rub == Decimal("0.0000")
