import uuid

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_regenerate_reuses_original_attachment_ids_for_preview_and_prepare(monkeypatch):
    user = User.objects.create_user(
        username="retry-files-user",
        email="retry-files@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Retry files")
    source = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Разбери приложенный документ",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.FAILED,
    )
    first = uuid.uuid4()
    second = uuid.uuid4()
    original = Generation.objects.create(
        owner=user,
        user_message=source,
        assistant_message=assistant,
        state=Generation.State.FAILED,
        model="test-model",
        routed_model="test-model",
        idempotency_key="original-with-files",
        error_code="provider_unavailable",
        context_snapshot={
            "attached_files": [
                {"file_id": str(first), "file_name": "one.pdf"},
                {"file_id": str(second), "file_name": "two.pdf"},
            ]
        },
    )
    observed = {"preview": None, "prepare": None}

    def fake_guard(_request, *, user, conversation, content, file_ids=None):
        observed["preview"] = list(file_ids or [])
        return None

    def fake_prepare(*, file_ids=None, **_kwargs):
        observed["prepare"] = list(file_ids or [])
        return original, False

    monkeypatch.setattr("apps.chat.message_actions._cost_guard", fake_guard)
    monkeypatch.setattr("apps.chat.message_actions.prepare", fake_prepare)
    monkeypatch.setattr(
        "apps.chat.message_actions.RegenerateMessageView._fork_before",
        lambda self, **_kwargs: None,
    )

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{assistant.id}/regenerate/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"retry-files:{uuid.uuid4()}",
    )

    assert response.status_code == 200
    assert observed["preview"] == [str(first), str(second)]
    assert observed["prepare"] == [str(first), str(second)]
