import uuid

import pytest

from apps.accounts.models import User

from .durable_follow import follow_existing_generation
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_reconnect_never_exposes_internal_provider_error_code():
    user = User.objects.create_user(
        username="reconnect-public-error",
        email="reconnect-public-error@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Reconnect failure")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Запрос",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.FAILED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.FAILED,
        model="system-pro",
        idempotency_key="reconnect-internal-provider-error",
        error_code="gigachat_quota_exhausted",
    )

    chunks = list(follow_existing_generation(generation))
    body = "".join(chunks)

    assert chunks[0].startswith("event: generation\n")
    assert chunks[-1].startswith("event: error\n")
    assert '"code": "AI-102"' in chunks[-1]
    assert '"support_code": "AI-102"' in chunks[-1]
    assert "gigachat_quota_exhausted" not in body
