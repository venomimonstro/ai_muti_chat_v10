import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_prepare_failure_after_generation_creation_returns_durable_terminal_stream(monkeypatch):
    user = User.objects.create_user(
        username="durable-preflight-user",
        email="durable-preflight@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Durable preflight")
    client_message_id = uuid.uuid4()
    key = "durable-preflight:request"
    content = "Отправь запрос один раз"

    monkeypatch.setattr(
        "apps.chat.cost_views.chat_cost_preview",
        lambda **_kwargs: {
            "estimated_min_rub": Decimal("0.0000"),
            "estimated_max_rub": Decimal("0.1000"),
            "confirmation_required": False,
            "confirmation_threshold_rub": Decimal("20.0000"),
            "selected_model": "test-model",
            "models": ["test-model"],
            "spend_guard": {},
            "blocked_by_spend_guard": False,
            "spend_guard_message": "",
        },
    )

    def failed_prepare(*, user, conversation, idempotency_key, **_kwargs):
        user_message = Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        assistant = Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="",
            status=Message.Status.FAILED,
        )
        Generation.objects.create(
            owner=user,
            user_message=user_message,
            assistant_message=assistant,
            state=Generation.State.FAILED,
            model="test-model",
            routed_model="test-model",
            idempotency_key=idempotency_key,
            error_code="preflight_no_model",
        )
        raise ValidationError("Нет доступной модели для выполнения запроса")

    monkeypatch.setattr("apps.chat.cost_views.prepare", failed_prepare)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": content,
            "client_message_id": str(client_message_id),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: generation" in body
    assert "event: error" in body
    assert Generation.objects.filter(owner=user, idempotency_key=key).count() == 1
    assert Message.objects.filter(
        conversation=conversation,
        role=Message.Role.USER,
        content=content,
    ).count() == 1
