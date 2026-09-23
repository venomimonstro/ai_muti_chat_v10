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
def test_legacy_placeholder_is_only_allowed_for_auto_mode():
    auto = ConversationSerializer(data={"title": "AUTO", "routing_mode": "economy", "selected_model": "echo-v1"})
    assert auto.is_valid(), auto.errors

    manual = ConversationSerializer(data={"title": "Manual", "routing_mode": "manual", "selected_model": "echo-v1"})
    assert not manual.is_valid()
    assert "selected_model" in manual.errors
