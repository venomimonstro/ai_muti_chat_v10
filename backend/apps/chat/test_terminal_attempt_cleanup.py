import uuid

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import Provider

from .models import Conversation, Generation, GenerationAttempt, Message


def _generation_with_running_attempt(*, suffix: str):
    user = User.objects.create_user(
        username=f"attempt-cleanup-{suffix}",
        email=f"attempt-cleanup-{suffix}@example.test",
        password="password123!",
    )
    provider = Provider.objects.create(
        slug=f"attempt-cleanup-provider-{suffix}",
        name="Attempt cleanup provider",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    conversation = Conversation.objects.create(owner=user, title="Attempt cleanup")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="test",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.STREAMING,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="echo-v1",
        idempotency_key=f"attempt-cleanup:{suffix}",
    )
    attempt = GenerationAttempt.objects.create(
        generation=generation,
        provider=provider,
        model_slug="echo-v1",
        sequence=1,
        state=GenerationAttempt.State.RUNNING,
    )
    return generation, attempt


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("terminal_state", "error_code"),
    [
        (Generation.State.FAILED, "unexpected_sdk_failure"),
        (Generation.State.CANCELLED, "client_cancelled"),
    ],
)
def test_terminal_generation_closes_orphaned_running_attempt(terminal_state, error_code):
    generation, attempt = _generation_with_running_attempt(
        suffix=f"{terminal_state}-{uuid.uuid4().hex[:8]}"
    )

    generation.state = terminal_state
    generation.error_code = error_code
    generation.save(update_fields=["state", "error_code"])

    attempt.refresh_from_db()
    assert attempt.state == GenerationAttempt.State.FAILED
    assert attempt.error_code == error_code
    assert attempt.retryable is False
    assert attempt.finished_at is not None
