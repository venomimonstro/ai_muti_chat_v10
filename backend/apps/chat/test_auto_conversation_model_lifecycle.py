import pytest

from apps.accounts.models import User

from .models import Conversation
from .serializers import ConversationSerializer


@pytest.mark.django_db
def test_auto_conversation_does_not_require_client_visible_manual_model():
    user = User.objects.create_user(username="auto-no-model", email="auto-no-model@example.com", password="password123")
    serializer = ConversationSerializer(data={"title": "AUTO", "routing_mode": "balanced"})
    serializer.is_valid(raise_exception=True)

    conversation = serializer.save(owner=user)

    assert conversation.routing_mode == Conversation.RoutingMode.BALANCED
    assert conversation.selected_model == "echo-v1"


@pytest.mark.django_db
def test_auto_explicit_legacy_placeholder_validates_without_client_model_lookup():
    serializer = ConversationSerializer(
        data={"title": "AUTO", "routing_mode": "economy", "selected_model": "echo-v1"}
    )
    assert serializer.is_valid(), serializer.errors
