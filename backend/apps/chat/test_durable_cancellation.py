import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import User

from . import cancellation
from .models import Conversation, Generation, Message
from .ux_models import ChatCancellationMarker


def _generation(owner, conversation, *, key, state=Generation.State.RUNNING):
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Останови этот запрос",
        status=Message.Status.SAVED,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.STREAMING,
    )
    return Generation.objects.create(
        owner=owner,
        user_message=user_message,
        assistant_message=assistant_message,
        state=state,
        model="system-pro",
        idempotency_key=key,
    )


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

    generation = _generation(user, conversation, key=key, state=Generation.State.QUEUED)

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
    generation = _generation(second, conversation, key=shared_key, state=Generation.State.QUEUED)

    cancellation._DB_CHECKS.clear()
    assert cancellation.cancel_requested(generation) is False


@pytest.mark.django_db(transaction=True)
def test_running_generation_can_be_cancelled_by_generation_id_after_page_reload():
    user = User.objects.create_user(
        username="reload-cancel-owner",
        email="reload-cancel-owner@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Reload cancel")
    generation = _generation(
        user,
        conversation,
        key="workspace:reload-cancel",
        state=Generation.State.RUNNING,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        reverse("chat-generation-cancel", kwargs={"conversation_id": conversation.id}),
        {"generation_id": str(generation.id)},
        format="json",
    )

    assert response.status_code == 202
    assert response.json()["generation_id"] == str(generation.id)
    assert response.json()["state"] == Generation.State.RUNNING
    assert ChatCancellationMarker.objects.filter(
        owner=user,
        generation_id=generation.id,
        idempotency_key=generation.idempotency_key,
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_generation_id_cancel_cannot_cross_tenants_or_conversations():
    owner = User.objects.create_user(
        username="reload-cancel-owner-2",
        email="reload-cancel-owner-2@example.test",
        password="password123!",
    )
    other = User.objects.create_user(
        username="reload-cancel-other",
        email="reload-cancel-other@example.test",
        password="password123!",
    )
    owner_conversation = Conversation.objects.create(owner=owner, title="Owner chat")
    other_conversation = Conversation.objects.create(owner=other, title="Other chat")
    generation = _generation(
        owner,
        owner_conversation,
        key="workspace:reload-cancel-owner",
        state=Generation.State.RUNNING,
    )

    client = APIClient()
    client.force_authenticate(user=other)
    response = client.post(
        reverse("chat-generation-cancel", kwargs={"conversation_id": other_conversation.id}),
        {"generation_id": str(generation.id)},
        format="json",
    )

    assert response.status_code == 404
    assert not ChatCancellationMarker.objects.filter(
        owner=other,
        generation_id=generation.id,
    ).exists()
