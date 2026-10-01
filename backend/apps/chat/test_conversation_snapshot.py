from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import conversation_snapshot


def test_prepare_refreshes_conversation_before_entering_runtime_pipeline():
    raw_prepare = Mock(return_value=("generation", True))
    streaming = SimpleNamespace(prepare=raw_prepare)
    old = SimpleNamespace(pk="conversation-1", routing_mode="balanced")
    fresh = SimpleNamespace(pk="conversation-1", routing_mode="manual")
    user = SimpleNamespace(pk="user-1")

    queryset = Mock()
    queryset.filter.return_value.first.return_value = fresh
    with patch.object(
        conversation_snapshot.Conversation.objects,
        "select_related",
        return_value=queryset,
    ):
        conversation_snapshot.install(streaming)
        result = streaming.prepare(
            user=user,
            conversation=old,
            idempotency_key="key-1",
            content="hello",
            client_message_id="message-1",
        )

    assert result == ("generation", True)
    raw_prepare.assert_called_once()
    assert raw_prepare.call_args.kwargs["conversation"] is fresh
    assert raw_prepare.call_args.kwargs["user"] is user
