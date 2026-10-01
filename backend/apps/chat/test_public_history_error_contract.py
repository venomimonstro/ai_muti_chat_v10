import pytest

from apps.accounts.models import User

from .models import Conversation, Generation, Message
from .serializers import MessageSerializer


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("internal_code", "public_code"),
    [
        ("gigachat_quota_exhausted", "AI-102"),
        ("credit_balance_exhausted", "AI-102"),
        ("stream_runtime_failed", "AI-103"),
        ("partial_response_interrupted", "partial_response_interrupted"),
        ("client_cancelled", "client_cancelled"),
    ],
)
def test_message_history_never_exposes_internal_generation_error_code(
    internal_code,
    public_code,
):
    user = User.objects.create_user(
        username=f"history-error-{internal_code[:20]}",
        email=f"{internal_code[:20]}@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Error contract")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="test",
        status=Message.Status.SAVED,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.FAILED,
    )
    Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant_message,
        state=Generation.State.FAILED,
        model="model-a",
        idempotency_key=f"error-contract:{internal_code}",
        error_code=internal_code,
    )

    payload = MessageSerializer(assistant_message).data

    assert payload["generation"]["error_code"] == public_code
    if internal_code != public_code:
        assert internal_code not in str(payload)
