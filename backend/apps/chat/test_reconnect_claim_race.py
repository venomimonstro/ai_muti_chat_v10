import uuid

import pytest

from apps.accounts.models import User

from .managed_stream import managed_run
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_follower_that_loses_generation_claim_cannot_fail_active_producer():
    user = User.objects.create_user(
        username="claim-race-user",
        email="claim-race@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Claim race")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Продолжи единственный активный ответ",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.STREAMING,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="already-owned-by-another-producer",
        idempotency_key="claim-race:request",
    )

    body = "".join(managed_run(generation))

    generation.refresh_from_db()
    assistant.refresh_from_db()

    assert "generation_in_progress" in body
    assert generation.state == Generation.State.RUNNING
    assert generation.error_code == ""
    assert assistant.status == Message.Status.STREAMING
