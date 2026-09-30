from types import SimpleNamespace
import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from . import single_flight
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_prepare_falls_back_to_database_lock_when_cache_is_down(monkeypatch):
    user = User.objects.create_user(
        username="cache-fallback",
        email="cache-fallback@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Cache fallback")
    calls = []

    def raw_prepare(**kwargs):
        calls.append(kwargs["idempotency_key"])
        return "prepared", True

    module = SimpleNamespace(prepare=raw_prepare)
    single_flight.install(module)
    monkeypatch.setattr(single_flight, "_cache_lock_acquire", lambda _key: None)

    result = module.prepare(
        user=user,
        conversation=conversation,
        content="Проверка без Redis",
        client_message_id=uuid.uuid4(),
        idempotency_key="cache-down-prepare",
        file_ids=[],
    )

    assert result == ("prepared", True)
    assert calls == ["cache-down-prepare"]


@pytest.mark.django_db(transaction=True)
def test_database_fallback_still_blocks_second_active_generation(monkeypatch):
    user = User.objects.create_user(
        username="cache-fallback-active",
        email="cache-fallback-active@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Active generation")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Первый запрос",
        client_message_id=uuid.uuid4(),
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
    )
    Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="system-pro",
        idempotency_key="already-running",
    )

    module = SimpleNamespace(prepare=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")))
    single_flight.install(module)
    monkeypatch.setattr(single_flight, "_cache_lock_acquire", lambda _key: None)

    with pytest.raises(ValidationError, match="Предыдущий ответ ещё формируется"):
        module.prepare(
            user=user,
            conversation=conversation,
            content="Второй запрос",
            client_message_id=uuid.uuid4(),
            idempotency_key="second-request",
            file_ids=[],
        )
