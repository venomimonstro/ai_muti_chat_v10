import uuid

import pytest

from apps.accounts.models import User

from .activity_stream import managed_request_stream
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_running_idempotent_reconnect_follows_existing_generation(monkeypatch):
    user = User.objects.create_user(
        username="running-reconnect-user",
        email="running-reconnect@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Running reconnect")
    client_message_id = uuid.uuid4()
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Продолжай существующий ответ",
        client_message_id=client_message_id,
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Уже полученная часть ответа",
        status=Message.Status.STREAMING,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="system-pro",
        idempotency_key="running-reconnect-key",
        context_snapshot={"attached_files": [], "vision_assets": []},
    )

    def fake_prepare(**kwargs):
        return generation, False

    def fail_if_provider_path_runs(*args, **kwargs):
        raise AssertionError("reconnect must not start a second provider execution")

    def fake_follow(existing):
        assert existing.id == generation.id
        yield 'event: snapshot\ndata: {"text":"Уже полученная часть ответа","state":"running","reconnected":true}\n\n'
        yield 'event: heartbeat\ndata: {"state":"running","reconnected":true}\n\n'

    monkeypatch.setattr("apps.chat.activity_stream.prepare", fake_prepare)
    monkeypatch.setattr("apps.chat.activity_stream.managed_run", fail_if_provider_path_runs)
    monkeypatch.setattr("apps.chat.activity_stream.follow_existing_generation", fake_follow)

    body = "".join(
        managed_request_stream(
            user=user,
            conversation=conversation,
            idempotency_key=generation.idempotency_key,
            payload={
                "content": user_message.content,
                "client_message_id": client_message_id,
                "file_ids": [],
            },
        )
    )

    generation.refresh_from_db()
    assistant.refresh_from_db()

    assert "reconnecting" in body
    assert "Уже полученная часть ответа" in body
    assert "generation_in_progress" not in body
    assert generation.state == Generation.State.RUNNING
    assert assistant.status == Message.Status.STREAMING
