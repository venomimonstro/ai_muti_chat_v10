import pytest
from rest_framework.test import APIRequestFactory

from apps.accounts.models import User

from .models import Conversation
from .serializers import ConversationSerializer


@pytest.mark.django_db
def test_new_client_chat_defaults_to_system_pro_auto_routing():
    user = User.objects.create_user(
        username="default-routing-user",
        email="default-routing@example.test",
        password="password123",
    )
    request = APIRequestFactory().post("/api/v1/conversations/", {}, format="json")
    request.user = user

    serializer = ConversationSerializer(
        data={"title": "Новый чат"},
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    conversation = serializer.save(owner=user)

    assert conversation.routing_mode == Conversation.RoutingMode.BALANCED
    assert conversation.selected_model == "echo-v1"
