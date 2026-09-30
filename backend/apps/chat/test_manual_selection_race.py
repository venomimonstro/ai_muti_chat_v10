import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation


@pytest.mark.django_db
def test_new_manual_chat_survives_model_disappearing_after_picker_refresh():
    user = User.objects.create_user(
        username="manual-create-race",
        email="manual-create-race@example.test",
        password="password123!",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/conversations/",
        {
            "title": "Новый чат",
            "routing_mode": "manual",
            "selected_model": "model-that-disappeared-after-picker-refresh",
        },
        format="json",
    )

    assert response.status_code == 201
    conversation = Conversation.objects.get(pk=response.data["id"])
    assert conversation.routing_mode == Conversation.RoutingMode.MANUAL
    assert conversation.selected_model == "model-that-disappeared-after-picker-refresh"


@pytest.mark.django_db
def test_existing_chat_cannot_be_explicitly_switched_to_dead_manual_model():
    user = User.objects.create_user(
        username="manual-patch-race",
        email="manual-patch-race@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="Existing",
        routing_mode=Conversation.RoutingMode.AUTO,
        selected_model="echo-v1",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/conversations/{conversation.id}/",
        {"routing_mode": "manual", "selected_model": "dead-manual-model"},
        format="json",
    )

    assert response.status_code == 400
    conversation.refresh_from_db()
    assert conversation.routing_mode == Conversation.RoutingMode.AUTO
    assert conversation.selected_model == "echo-v1"
