import uuid

import pytest

from apps.accounts.models import User

from .attachment_durability import _persist_failed_request_files
from .message_actions import _generation_file_ids_for_user_message
from .models import Conversation, Generation, Message


@pytest.mark.django_db
def test_failed_preflight_persists_exact_attachment_identity():
    user = User.objects.create_user(
        username="attachment-durable-user",
        email="attachment-durable@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Attachment durability")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Разбери файл",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.FAILED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.FAILED,
        model="test-model",
        idempotency_key="attachment-durable-key",
        context_snapshot={},
    )
    first = uuid.uuid4()
    second = uuid.uuid4()

    _persist_failed_request_files(
        user=user,
        idempotency_key=generation.idempotency_key,
        file_ids=[first, second],
    )
    generation.refresh_from_db()

    assert generation.context_snapshot["attached_files"] == [
        {"file_id": str(first)},
        {"file_id": str(second)},
    ]
    assert _generation_file_ids_for_user_message(user_message) == [str(first), str(second)]


@pytest.mark.django_db
def test_durability_never_overwrites_richer_success_snapshot():
    user = User.objects.create_user(
        username="attachment-rich-user",
        email="attachment-rich@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Attachment rich")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Разбери файл",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.FAILED,
    )
    original = uuid.uuid4()
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.FAILED,
        model="test-model",
        idempotency_key="attachment-rich-key",
        context_snapshot={
            "attached_files": [
                {"file_id": str(original), "file_name": "document.pdf", "sha256": "abc"}
            ]
        },
    )

    _persist_failed_request_files(
        user=user,
        idempotency_key=generation.idempotency_key,
        file_ids=[uuid.uuid4()],
    )
    generation.refresh_from_db()

    assert generation.context_snapshot["attached_files"] == [
        {"file_id": str(original), "file_name": "document.pdf", "sha256": "abc"}
    ]
