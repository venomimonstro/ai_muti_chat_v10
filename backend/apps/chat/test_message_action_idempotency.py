import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from . import message_actions
from .branches import ensure_active_branch
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_regenerate_same_key_replays_without_second_branch(monkeypatch):
    user = User.objects.create_user(username="regen-replay", email="regen-replay@example.test")
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.BALANCED,
        selected_model="echo-v1",
    )
    branch = ensure_active_branch(conversation, user)
    conversation.refresh_from_db()
    source = Message.objects.create(
        conversation=conversation,
        branch=branch,
        role=Message.Role.USER,
        content="Первый запрос",
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        branch=branch,
        role=Message.Role.ASSISTANT,
        content="Первый ответ",
        status=Message.Status.COMPLETED,
    )

    monkeypatch.setattr(message_actions, "_cost_guard", lambda *args, **kwargs: None)

    def fake_prepare(*, user, conversation, content, client_message_id, idempotency_key, file_ids):
        request_message = Message.objects.create(
            conversation=conversation,
            branch=conversation.active_branch,
            role=Message.Role.USER,
            content=content,
            client_message_id=client_message_id,
            status=Message.Status.SAVED,
        )
        response_message = Message.objects.create(
            conversation=conversation,
            branch=conversation.active_branch,
            role=Message.Role.ASSISTANT,
            content="Новый ответ",
            status=Message.Status.COMPLETED,
        )
        generation = Generation.objects.create(
            owner=user,
            user_message=request_message,
            assistant_message=response_message,
            state=Generation.State.COMPLETED,
            model="System Pro",
            routed_model="System Pro",
            provider_slug="system",
            idempotency_key=idempotency_key,
            actual_cost_rub=Decimal("1.0000"),
        )
        return generation, True

    monkeypatch.setattr(message_actions, "prepare", fake_prepare)
    monkeypatch.setattr(message_actions, "run", lambda generation: iter(()))

    client = APIClient()
    client.force_authenticate(user)
    url = f"/api/v1/conversations/{conversation.id}/messages/{assistant.id}/regenerate/"

    first = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="regen-stable-key")
    assert first.status_code == 200
    assert conversation.branches.count() == 2
    assert Generation.objects.filter(owner=user, idempotency_key="regen-stable-key").count() == 1

    # The original assistant is no longer visible on the new active branch. Replay
    # must therefore be resolved by idempotency before target visibility lookup.
    second = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY="regen-stable-key")
    assert second.status_code == 200
    assert conversation.branches.count() == 2
    assert Generation.objects.filter(owner=user, idempotency_key="regen-stable-key").count() == 1
    assert Message.objects.filter(conversation=conversation).count() == 4

    wrong_source = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{uuid.uuid4()}/regenerate/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="regen-stable-key",
    )
    assert wrong_source.status_code == 400


@pytest.mark.django_db(transaction=True)
def test_action_client_message_id_is_stable_and_bound_to_source():
    user = User.objects.create_user(username="action-id", email="action-id@example.test")
    first_source = uuid.uuid4()
    second_source = uuid.uuid4()
    first = message_actions._action_client_message_id(user, "regenerate", "same-key", first_source)
    replay = message_actions._action_client_message_id(user, "regenerate", "same-key", first_source)
    other_source = message_actions._action_client_message_id(user, "regenerate", "same-key", second_source)
    other_action = message_actions._action_client_message_id(user, "edit", "same-key", first_source)

    assert first == replay
    assert first != other_source
    assert first != other_action
