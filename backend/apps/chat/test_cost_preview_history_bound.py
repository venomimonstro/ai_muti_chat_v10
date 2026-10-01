from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.accounts.models import User

from . import cost_preview
from .models import Conversation, Message


@pytest.mark.django_db
@override_settings(SMART_CONTEXT_RECENT_TURNS=3)
def test_cost_preview_history_is_bounded_to_recent_turn_window():
    user = User.objects.create_user(
        username="preview-history-user",
        email="preview-history@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Long chat")
    for index in range(20):
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER if index % 2 == 0 else Message.Role.ASSISTANT,
            content=f"message-{index}",
            status=Message.Status.COMPLETED,
        )

    captured = []

    def fake_estimate(messages):
        captured.extend(messages)
        return len(messages)

    with patch.object(cost_preview, "estimate_message_tokens", side_effect=fake_estimate):
        count = cost_preview._existing_history_tokens(conversation)

    assert count == 6
    assert [row["content"] for row in captured] == [
        "message-14",
        "message-15",
        "message-16",
        "message-17",
        "message-18",
        "message-19",
    ]
