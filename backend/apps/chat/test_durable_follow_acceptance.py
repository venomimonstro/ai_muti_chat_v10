import uuid

import pytest

from apps.accounts.models import User

from .durable_follow import follow_existing_generation
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_reconnect_emits_generation_acceptance_before_terminal_snapshot():
    user = User.objects.create_user(
        username="reconnect-acceptance",
        email="reconnect-acceptance@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Reconnect acceptance")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Уже принятый запрос",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Готовый ответ",
        status=Message.Status.COMPLETED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.COMPLETED,
        model="system-pro",
        idempotency_key="reconnect-acceptance-key",
    )

    chunks = list(follow_existing_generation(generation))

    assert chunks[0].startswith("event: generation\n")
    assert f'"id": "{generation.id}"' in chunks[0]
    assert '"reconnected": true' in chunks[0]
    assert any(chunk.startswith("event: snapshot\n") for chunk in chunks[1:])
    assert chunks[-1].startswith("event: completed\n")
