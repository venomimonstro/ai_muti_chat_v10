import pytest

from apps.accounts.models import User

from . import cancellation
from .models import Conversation, Generation, Message
from .ux_models import ChatCancellationMarker


@pytest.mark.django_db(transaction=True)
def test_cancel_marker_survives_cache_failure_and_precedes_generation(monkeypatch):
    user = User.objects.create_user(
        username="durable-cancel",
        email="durable-cancel@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Durable cancel")
    key = "workspace:durable-cancel-test"

    def cache_down(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(cancellation.cache, "set", cache_down)
    monkeypatch.setattr(cancellation.cache, "get", cache_down)
    monkeypatch.setattr(cancellation.cache, "delete", cache_down)

    cancellation.request_cancel(
        owner_id=user.id,
        idempotency_key=key,
        generation_id=None,
    )
    assert ChatCancellationMarker.objects.filter(owner=user).count() == 1

    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Останови этот запрос",
        status=Message.Status.SAVED,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.SAVED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant_message,
        state=Generation.State.QUEUED,
        model="system-pro",
        idempotency_key=key,
    )

    cancellation._DB_CHECKS.clear()
    assert cancellation.cancel_requested(generation) is True

    cancellation.clear_cancel(generation)
    cancellation._DB_CHECKS.clear()
    assert ChatCancellationMarker.objects.filter(owner=user).count() == 0
    assert cancellation.cancel_requested(generation) is False


@pytest.mark.django_db(transaction=True)
def test_cancel_markers_are_tenant_scoped(monkeypatch):
    first = User.objects.create_user(
        username="cancel-first",
        email="cancel-first@example.test",
        password="password123!",
    )
    second = User.objects.create_user(
        username="cancel-second",
        email="cancel-second@example.test",
        password="password123!",
    )
    shared_key = "same-client-key"

    monkeypatch.setattr(cancellation.cache, "get", lambda *args, **kwargs: None)
    monkeypatch.setattr(cancellation.cache, "set", lambda *args, **kwargs: None)
    monkeypatch.setattr(cancellation.cache, "delete", lambda *args, **kwargs: None)

    cancellation.request_cancel(
        owner_id=first.id,
        idempotency_key=shared_key,
        generation_id=None,
    )

    conversation = Conversation.objects.create(owner=second, title="Other tenant")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Не должен быть отменён",
        status=Message.Status.SAVED,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.SAVED,
    )
    generation = Generation.objects.create(
        owner=second,
        user_message=user_message,
        assistant_message=assistant_message,
        state=Generation.State.QUEUED,
        model="system-pro",
        idempotency_key=shared_key,
    )

    cancellation._DB_CHECKS.clear()
    assert cancellation.cancel_requested(generation) is False
