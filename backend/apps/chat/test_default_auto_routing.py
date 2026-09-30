import pytest
from django.contrib.auth import get_user_model

from .models import Conversation
from .serializers import ConversationSerializer


@pytest.mark.django_db
def test_legacy_new_chat_payload_is_normalized_to_auto():
    user = get_user_model().objects.create_user(username="auto-default", password="secret12345")
    serializer = ConversationSerializer(
        data={
            "title": "Новый чат",
            "routing_mode": Conversation.RoutingMode.BALANCED,
            "selected_model": "some-client-default-model",
        }
    )
    assert serializer.is_valid(), serializer.errors

    conversation = serializer.save(owner=user)

    assert conversation.routing_mode == Conversation.RoutingMode.AUTO
    assert conversation.selected_model == "echo-v1"


@pytest.mark.django_db
def test_explicit_balanced_creation_without_legacy_selected_model_stays_balanced():
    user = get_user_model().objects.create_user(username="explicit-balanced", password="secret12345")
    serializer = ConversationSerializer(
        data={
            "title": "Рабочий чат",
            "routing_mode": Conversation.RoutingMode.BALANCED,
        }
    )
    assert serializer.is_valid(), serializer.errors

    conversation = serializer.save(owner=user)

    assert conversation.routing_mode == Conversation.RoutingMode.BALANCED
    assert conversation.selected_model == "echo-v1"
