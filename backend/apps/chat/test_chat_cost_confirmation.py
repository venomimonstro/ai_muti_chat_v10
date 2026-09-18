from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion
from apps.billing.services import credit

from .models import Conversation, Generation, Message


@pytest.fixture
def expensive_chat(settings):
    settings.CHAT_CONFIRM_THRESHOLD_RUB = "1.00"
    user = User.objects.create_user(
        username="expensive-chat",
        email="expensive-chat@example.test",
        password="password123!",
    )
    credit(user, Decimal("100"), "test", "expensive-chat")
    provider = Provider.objects.create(
        slug="expensive-echo",
        name="Expensive Echo",
        adapter_type=Provider.AdapterType.ECHO,
    )
    AIModel.objects.create(
        provider=provider,
        slug="expensive-echo-v1",
        display_name="Expensive Echo",
        upstream_model="expensive-echo-v1",
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug="expensive-echo-v1",
        input_rub_per_million=Decimal("5000"),
        output_rub_per_million=Decimal("5000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(
        owner=user,
        selected_model="expensive-echo-v1",
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    client = APIClient()
    client.force_authenticate(user)
    return client, user, conversation


@pytest.mark.django_db(transaction=True)
def test_expensive_stream_is_rejected_before_generation_or_reservation(expensive_chat):
    client, user, conversation = expensive_chat
    payload = {
        "content": "Сделай подробный анализ проекта",
        "client_message_id": "106a13bf-66da-4946-8863-08ddb7ec31c2",
    }

    preview = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/preview/",
        payload,
        format="json",
    )
    assert preview.status_code == 200
    assert preview.data["confirmation_required"] is True

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="expensive:blocked",
    )

    assert response.status_code == 409
    assert response.data["code"] == "cost_confirmation_required"
    assert Generation.objects.filter(owner=user).count() == 0
    assert Message.objects.filter(conversation=conversation).count() == 0
    assert BalanceReservation.objects.filter(wallet=user.wallet).count() == 0
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("100.0000")


@pytest.mark.django_db(transaction=True)
def test_confirmed_expensive_stream_runs_and_releases_reservation(expensive_chat):
    client, user, conversation = expensive_chat
    payload = {
        "content": "Сделай подробный анализ проекта",
        "client_message_id": "76f9c7b4-f3b0-429e-abd7-9f6c9ebd973d",
        "confirm_cost": True,
    }

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY="expensive:confirmed",
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: completed" in body
    assert Generation.objects.filter(owner=user).count() == 1
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub < Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
