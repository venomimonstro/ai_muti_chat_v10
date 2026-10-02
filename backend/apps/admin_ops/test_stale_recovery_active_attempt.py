from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve
from apps.chat.models import Conversation, Generation, GenerationAttempt, Message

from .recovery import recover_stale_operations


@pytest.mark.django_db(transaction=True)
def test_old_generation_with_fresh_running_fallback_attempt_is_not_recovered(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="recovery-fresh-attempt",
        email="recovery-fresh-attempt@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "recovery-fresh-attempt")
    conversation = Conversation.objects.create(owner=user, title="Long failover")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="continue",
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
        model="fallback-model",
        idempotency_key="fresh-fallback-client",
    )
    reservation = reserve(user, Decimal("25"), f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    Generation.objects.filter(pk=generation.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )
    provider = Provider.objects.create(
        slug="fresh-fallback-provider",
        name="Fresh Fallback Provider",
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    GenerationAttempt.objects.create(
        generation=generation,
        provider=provider,
        model_slug="fallback-model",
        sequence=3,
        state=GenerationAttempt.State.RUNNING,
    )

    result = recover_stale_operations()

    generation.refresh_from_db()
    reservation.refresh_from_db()
    assert result["generations"] == 0
    assert generation.state == Generation.State.RUNNING
    assert reservation.state == BalanceReservation.State.ACTIVE


@pytest.mark.django_db(transaction=True)
def test_chat_specific_stale_timeout_is_not_overridden_by_general_operation_timeout(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 900
    settings.CHAT_GENERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="recovery-chat-specific-cutoff",
        email="recovery-chat-specific-cutoff@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "recovery-chat-specific-cutoff")
    conversation = Conversation.objects.create(owner=user, title="Chat stale cutoff")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="recover me",
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
        model="stale-model",
        idempotency_key="chat-specific-cutoff",
    )
    reservation = reserve(user, Decimal("5"), f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    Generation.objects.filter(pk=generation.pk).update(
        created_at=timezone.now() - timedelta(minutes=2)
    )

    result = recover_stale_operations()

    generation.refresh_from_db()
    reservation.refresh_from_db()
    assert result["generations"] == 1
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "stale_operation_recovered"
    assert reservation.state == BalanceReservation.State.RELEASED
