from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit

from .models import Conversation, Generation, Message


@pytest.fixture
def client_journey():
    user = User.objects.create_user(
        username="journey-user",
        email="journey@example.test",
        password="password123!",
    )
    credit(user, Decimal("50"), "test", "client-journey")
    provider = Provider.objects.create(
        slug="journey-echo",
        name="Journey Echo",
        adapter_type=Provider.AdapterType.ECHO,
    )
    AIModel.objects.create(
        provider=provider,
        slug="journey-echo-v1",
        display_name="Journey Echo",
        upstream_model="journey-echo-v1",
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug="journey-echo-v1",
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    client = APIClient()
    client.force_authenticate(user)
    return client, user


@pytest.mark.django_db(transaction=True)
def test_client_can_write_receive_organize_and_review_usage(client_journey):
    client, user = client_journey

    folder = client.post(
        "/api/v1/conversation-folders/",
        {"name": "Работа"},
        format="json",
    )
    assert folder.status_code == 201

    created = client.post(
        "/api/v1/conversations/",
        {
            "title": "Проверка маркетинга",
            "selected_model": "journey-echo-v1",
            "routing_mode": "manual",
            "memory_enabled": True,
        },
        format="json",
    )
    assert created.status_code == 201
    conversation_id = created.data["id"]

    moved = client.patch(
        f"/api/v1/conversation-ui/{conversation_id}/",
        {"folder": folder.data["id"], "is_pinned": True},
        format="json",
    )
    assert moved.status_code == 200
    assert moved.data["is_pinned"] is True

    draft_text = "Составь короткий план продвижения магазина слуховых аппаратов"
    saved_draft = client.put(
        f"/api/v1/conversations/{conversation_id}/draft/",
        {"content": draft_text},
        format="json",
    )
    assert saved_draft.status_code == 200
    restored_draft = client.get(f"/api/v1/conversations/{conversation_id}/draft/")
    assert restored_draft.status_code == 200
    assert restored_draft.data["content"] == draft_text

    before_balance = user.wallet.available_rub
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages/stream/",
        {
            "content": draft_text,
            "client_message_id": "5bfcc7f2-26da-4d0b-b54f-619188a5f501",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="journey:first-message",
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: generation" in body
    assert "event: delta" in body
    assert "event: completed" in body
    assert "Тестовый ответ:" in body
    assert draft_text in body

    generation = Generation.objects.get(owner=user)
    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == Generation.State.COMPLETED
    assert generation.assistant_message.status == Message.Status.COMPLETED
    assert generation.assistant_message.content.startswith("Тестовый ответ:")
    assert generation.actual_cost_rub is not None
    assert generation.actual_cost_rub > 0
    assert user.wallet.available_rub < before_balance
    assert user.wallet.reserved_rub == Decimal("0.0000")

    workspace = client.get(f"/api/v1/conversation-workspace/{conversation_id}/?limit=60")
    assert workspace.status_code == 200
    contents = [item["content"] for item in workspace.data["conversation"]["messages"]]
    assert draft_text in contents
    assert any(value.startswith("Тестовый ответ:") for value in contents)

    summaries = client.get("/api/v1/conversation-summaries/")
    assert summaries.status_code == 200
    row = next(item for item in summaries.data if item["id"] == conversation_id)
    assert row["folder"] == folder.data["id"]
    assert row["is_pinned"] is True
    assert "messages" not in row

    search = client.get("/api/v1/search/", {"q": "маркетинг"})
    assert search.status_code == 200
    assert any(item.get("conversation_id") == conversation_id for item in search.data["results"])

    usage = client.get("/api/v1/auth/usage/")
    assert usage.status_code == 200
    assert usage.data["thirty_days"]["requests"] == 1
    assert Decimal(usage.data["thirty_days"]["cost_rub"]) > 0

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}/")
    assert deleted.status_code == 204
    assert Conversation.objects.filter(pk=conversation_id).exists()
    assert Message.objects.filter(conversation_id=conversation_id).count() == 2
    assert all(
        item["id"] != conversation_id
        for item in client.get("/api/v1/conversation-summaries/").data
    )
    search_after_delete = client.get("/api/v1/search/", {"q": "маркетинг"})
    assert all(
        item.get("conversation_id") != conversation_id
        for item in search_after_delete.data["results"]
    )
