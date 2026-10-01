import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation, Generation, Message


def _generation(owner, conversation, *, key, state):
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Продолжить",
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.STREAMING,
    )
    return Generation.objects.create(
        owner=owner,
        user_message=user_message,
        assistant_message=assistant,
        state=state,
        model="test-model",
        idempotency_key=key,
    )


@pytest.mark.django_db
def test_generation_status_distinguishes_active_terminal_and_missing_without_cross_tenant_leak():
    user = User.objects.create_user(
        username="pending-owner",
        email="pending-owner@example.test",
        password="password123!",
    )
    other = User.objects.create_user(
        username="pending-other",
        email="pending-other@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Pending status")
    generation = _generation(
        user,
        conversation,
        key="pending-status-key",
        state=Generation.State.RUNNING,
    )
    url = reverse("chat-generation-status", kwargs={"conversation_id": conversation.id})

    client = APIClient()
    client.force_authenticate(user=user)
    active = client.get(url, {"idempotency_key": generation.idempotency_key})
    assert active.status_code == 200
    assert active.json()["state"] == Generation.State.RUNNING
    assert active.json()["active"] is True
    assert active.json()["terminal"] is False

    generation.state = Generation.State.COMPLETED
    generation.save(update_fields=["state"])
    terminal = client.get(url, {"idempotency_key": generation.idempotency_key})
    assert terminal.status_code == 200
    assert terminal.json()["active"] is False
    assert terminal.json()["terminal"] is True

    missing = client.get(url, {"idempotency_key": "never-created"})
    assert missing.status_code == 200
    assert missing.json() == {"state": "missing", "active": False, "terminal": False}

    client.force_authenticate(user=other)
    isolated = client.get(url, {"idempotency_key": generation.idempotency_key})
    assert isolated.status_code == 200
    assert isolated.json() == {"state": "missing", "active": False, "terminal": False}
