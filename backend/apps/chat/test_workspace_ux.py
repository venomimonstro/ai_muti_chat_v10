import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.chat.ux_models import ConversationFolder, ConversationUIState


@pytest.mark.django_db
def test_folders_and_ui_state_are_owner_scoped():
    alice = User.objects.create_user(username="alice-ux", email="alice@example.test", password="test-password-123")
    bob = User.objects.create_user(username="bob-ux", email="bob@example.test", password="test-password-123")
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
    user = User.objects.create_user(username="summary-user", email="summary@example.test", password="test-password-123")
    Conversation.objects.create(owner=user, title="Лёгкий список")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/v1/conversation-summaries/")
    assert response.status_code == 200
    assert response.data[0]["title"] == "Лёгкий список"
    assert "messages" not in response.data[0]


@pytest.mark.django_db
def test_workspace_page_rejects_foreign_conversation():
    owner = User.objects.create_user(username="owner-ux", email="owner@example.test", password="test-password-123")
    stranger = User.objects.create_user(username="stranger-ux", email="stranger@example.test", password="test-password-123")
    conversation = Conversation.objects.create(owner=owner, title="Приватный")
    client = APIClient()
    client.force_authenticate(stranger)

    response = client.get(f"/api/v1/conversation-workspace/{conversation.id}/")
    assert response.status_code == 404
