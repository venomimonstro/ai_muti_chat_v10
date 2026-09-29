import uuid

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_existing_generation_reconnect_skips_fresh_provider_preflight(monkeypatch):
    user = User.objects.create_user(
        username="chat-reconnect",
        email="chat-reconnect@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Reconnect test")
    client_message_id = uuid.uuid4()
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Продолжи ответ после обрыва",
        client_message_id=client_message_id,
        status=Message.Status.SAVED,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Уже сохранённый ответ",
        status=Message.Status.COMPLETED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant_message,
        state=Generation.State.COMPLETED,
        model="system-pro",
        routed_model="system-pro",
        provider_slug="system",
        idempotency_key="reconnect-fast-path",
        actual_cost_rub=0,
        context_snapshot={"attached_files": [], "vision_assets": []},
    )

    def fail_if_preflight_runs(*args, **kwargs):
        raise AssertionError("reconnect must not rerun chat_cost_preview")

    monkeypatch.setattr("apps.chat.cost_views.chat_cost_preview", fail_if_preflight_runs)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": user_message.content,
            "client_message_id": str(client_message_id),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=generation.idempotency_key,
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: snapshot" in body
    assert "Уже сохранённый ответ" in body
    assert Generation.objects.filter(owner=user).count() == 1
