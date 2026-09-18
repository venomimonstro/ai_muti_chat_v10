import uuid
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit
from apps.chat.models import Conversation, Generation
from apps.chat.streaming import prepare, run


@pytest.mark.django_db(transaction=True)
def test_client_journey_chat_folder_draft_answer_usage_and_delete():
    user = User.objects.create_user(
        username="journey-user",
        email="journey@example.test",
        password="test-password-123",
    )
    credit(user, Decimal("25.00"), "test", "journey-credit")
    provider = Provider.objects.create(slug="echo-journey", name="Echo Journey")
    AIModel.objects.create(
        provider=provider,
        slug="echo-v1",
        display_name="Echo",
        upstream_model="echo-v1",
    )
    PriceVersion.objects.create(
        model_slug="echo-v1",
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user)

    folder = client.post(
        "/api/v1/conversation-folders/", {"name": "Работа"}, format="json"
    )
    assert folder.status_code == 201

    conversation_response = client.post(
        "/api/v1/conversations/",
        {
            "title": "Запуск продукта",
            "routing_mode": Conversation.RoutingMode.MANUAL,
            "selected_model": "echo-v1",
        },
        format="json",
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.data["id"]
    conversation = Conversation.objects.get(pk=conversation_id)

    organized = client.patch(
        f"/api/v1/conversation-ui/{conversation_id}/",
        {"folder": folder.data["id"], "is_pinned": True},
        format="json",
    )
    assert organized.status_code == 200

    draft_text = "Составь короткий план коммерческого запуска сервиса"
    saved_draft = client.put(
        f"/api/v1/conversations/{conversation_id}/draft/",
        {"content": draft_text},
        format="json",
    )
    assert saved_draft.status_code == 200
    assert client.get(f"/api/v1/conversations/{conversation_id}/draft/").data["content"] == draft_text

    balance_before = user.wallet.available_rub
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content=draft_text,
        client_message_id=uuid.uuid4(),
        idempotency_key="journey-generation",
    )
    assert created is True
    events = "".join(run(generation))
    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()

    assert generation.state == Generation.State.COMPLETED
    assert generation.assistant_message.content.startswith("Тестовый ответ:")
    assert "Составь короткий план коммерческого запуска сервиса" in generation.assistant_message.content
    assert "event: delta" in events
    assert "event: completed" in events
    assert generation.actual_cost_rub is not None
    assert generation.actual_cost_rub > 0
    assert user.wallet.available_rub < balance_before
    assert user.wallet.reserved_rub == Decimal("0.0000")

    usage = client.get("/api/v1/auth/usage/")
    assert usage.status_code == 200
    assert usage.data["thirty_days"]["requests"] == 1
    assert Decimal(usage.data["thirty_days"]["cost_rub"]) == generation.actual_cost_rub

    settings = client.patch(
        f"/api/v1/conversation-settings/{conversation_id}/",
        {"routing_mode": Conversation.RoutingMode.BALANCED, "title": "Коммерческий запуск"},
        format="json",
    )
    assert settings.status_code == 200
    assert settings.data["routing_mode"] == Conversation.RoutingMode.BALANCED
    assert settings.data["title"] == "Коммерческий запуск"

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}/")
    assert deleted.status_code == 204
    assert Conversation.objects.filter(pk=conversation_id).exists()
    assert client.get(f"/api/v1/conversation-workspace/{conversation_id}/").status_code == 404

    restored = client.patch(
        f"/api/v1/conversation-ui/{conversation_id}/",
        {"deleted": False},
        format="json",
    )
    assert restored.status_code == 200
    assert client.get(f"/api/v1/conversation-workspace/{conversation_id}/").status_code == 200
