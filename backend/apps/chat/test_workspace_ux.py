import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.chat.ux_models import ConversationFolder, ConversationUIState


@pytest.mark.django_db
def test_folders_and_ui_state_are_owner_scoped():
    alice = User.objects.create_user(
        username="alice-ux", email="alice@example.test", password="test-password-123"
    )
    bob = User.objects.create_user(
        username="bob-ux", email="bob@example.test", password="test-password-123"
    )
    conversation = Conversation.objects.create(owner=alice, title="Рабочий чат")
    bob_folder = ConversationFolder.objects.create(owner=bob, name="Чужая папка")
    client = APIClient()
    client.force_authenticate(alice)

    created = client.post("/api/v1/conversation-folders/", {"name": "Работа"}, format="json")
    assert created.status_code == 201
    folder_id = created.data["id"]

    moved = client.patch(
        f"/api/v1/conversation-ui/{conversation.id}/",
        {"folder": folder_id, "is_pinned": True},
        format="json",
    )
    assert moved.status_code == 200
    state = ConversationUIState.objects.get(conversation=conversation)
    assert str(state.folder_id) == folder_id
    assert state.is_pinned is True

    rejected = client.patch(
        f"/api/v1/conversation-ui/{conversation.id}/",
        {"folder": str(bob_folder.id)},
        format="json",
    )
    assert rejected.status_code == 400
    assert ConversationUIState.objects.get(conversation=conversation).folder_id == state.folder_id


@pytest.mark.django_db
def test_conversation_summaries_do_not_include_message_bodies():
    user = User.objects.create_user(
        username="summary-user", email="summary@example.test", password="test-password-123"
    )
    Conversation.objects.create(owner=user, title="Лёгкий список")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/v1/conversation-summaries/")
    assert response.status_code == 200
    assert response.data[0]["title"] == "Лёгкий список"
    assert "messages" not in response.data[0]


@pytest.mark.django_db
def test_workspace_page_is_bounded_and_can_load_older_messages():
    user = User.objects.create_user(
        username="long-chat", email="long@example.test", password="test-password-123"
    )
    conversation = Conversation.objects.create(owner=user, title="Очень длинный чат")
    Message.objects.bulk_create(
        [
            Message(
                conversation=conversation,
                role=Message.Role.USER if index % 2 == 0 else Message.Role.ASSISTANT,
                content=f"message-{index}",
            )
            for index in range(150)
        ]
    )
    client = APIClient()
    client.force_authenticate(user)

    first = client.get(f"/api/v1/conversation-workspace/{conversation.id}/?limit=60")
    assert first.status_code == 200
    assert len(first.data["conversation"]["messages"]) == 60
    assert first.data["has_more"] is True
    assert first.data["next_before"]

    second = client.get(
        f"/api/v1/conversation-workspace/{conversation.id}/",
        {"limit": 60, "before": first.data["next_before"]},
    )
    assert second.status_code == 200
    assert len(second.data["conversation"]["messages"]) == 60
    assert second.data["conversation"]["messages"][-1]["content"] != first.data["conversation"]["messages"][0]["content"]


@pytest.mark.django_db
def test_lightweight_settings_response_does_not_serialize_history():
    user = User.objects.create_user(
        username="settings-user", email="settings@example.test", password="test-password-123"
    )
    conversation = Conversation.objects.create(owner=user, title="До")
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content="private body")
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/conversation-settings/{conversation.id}/",
        {"title": "После", "routing_mode": Conversation.RoutingMode.BALANCED},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["title"] == "После"
    assert "messages" not in response.data
    assert "private body" not in str(response.data)


@pytest.mark.django_db
def test_soft_deleted_chat_disappears_without_destroying_messages():
    user = User.objects.create_user(
        username="delete-user", email="delete@example.test", password="test-password-123"
    )
    conversation = Conversation.objects.create(owner=user, title="Удалить меня")
    message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="Сохранить для аудита"
    )
    client = APIClient()
    client.force_authenticate(user)

    deleted = client.patch(
        f"/api/v1/conversation-ui/{conversation.id}/",
        {"deleted": True},
        format="json",
    )
    assert deleted.status_code == 200
    assert deleted.data["deleted"] is True
    summaries = client.get("/api/v1/conversation-summaries/")
    assert all(item["id"] != str(conversation.id) for item in summaries.data)
    workspace = client.get(f"/api/v1/conversation-workspace/{conversation.id}/")
    assert workspace.status_code == 404
    assert Message.objects.filter(pk=message.pk).exists()

    restored = client.patch(
        f"/api/v1/conversation-ui/{conversation.id}/",
        {"deleted": False},
        format="json",
    )
    assert restored.status_code == 200
    assert client.get(f"/api/v1/conversation-workspace/{conversation.id}/").status_code == 200


@pytest.mark.django_db
def test_deleted_chat_rejects_message_actions():
    user = User.objects.create_user(
        username="deleted-action-user",
        email="deleted-action@example.test",
        password="test-password-123",
    )
    conversation = Conversation.objects.create(owner=user, title="Удалённый чат")
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Исходный запрос",
    )
    client = APIClient()
    client.force_authenticate(user)
    assert client.delete(f"/api/v1/conversations/{conversation.id}/").status_code == 204

    edited = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/{message.id}/edit/",
        {"content": "Новый текст"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="deleted:edit",
    )
    assert edited.status_code == 404
    assert Message.objects.filter(conversation=conversation).count() == 1


@pytest.mark.django_db
def test_workspace_page_rejects_foreign_conversation():
    owner = User.objects.create_user(
        username="owner-ux", email="owner@example.test", password="test-password-123"
    )
    stranger = User.objects.create_user(
        username="stranger-ux", email="stranger@example.test", password="test-password-123"
    )
    conversation = Conversation.objects.create(owner=owner, title="Приватный")
    client = APIClient()
    client.force_authenticate(stranger)

    response = client.get(f"/api/v1/conversation-workspace/{conversation.id}/")
    assert response.status_code == 404
