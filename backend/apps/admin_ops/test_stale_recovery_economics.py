from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve
from apps.chat.models import CompareRun, Conversation, Generation, Message

from .recovery import recover_stale_operations


@pytest.mark.django_db(transaction=True)
def test_stale_chat_operation_releases_reservation_and_restores_wallet(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="recovery-chat", email="recovery-chat@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "recovery-chat")
    conversation = Conversation.objects.create(owner=user, title="Recovery")
    user_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="hello"
    )
    assistant = Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content="partial"
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="test",
        idempotency_key="recovery-chat-client",
    )
    reservation = reserve(user, Decimal("25"), f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    old = timezone.now() - timedelta(minutes=10)
    Generation.objects.filter(pk=generation.pk).update(created_at=old)

    result = recover_stale_operations()

    generation.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert result["generations"] == 1
    assert reservation.state == BalanceReservation.State.RELEASED
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "stale_operation_recovered"
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_stale_compare_synthesis_reservation_is_released_and_retry_becomes_possible(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="recovery-synthesis",
        email="recovery-synthesis@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "recovery-synthesis")
    conversation = Conversation.objects.create(owner=user, title="Compare")
    run = CompareRun.objects.create(
        owner=user,
        conversation=conversation,
        prompt="compare",
        idempotency_key="compare-run-client",
        state=CompareRun.State.COMPLETED,
        model_slugs=["a", "b"],
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
    )
    reservation = reserve(user, Decimal("10"), f"compare-synthesis:{run.id}")
    run.synthesis_reservation_id = reservation.id
    run.synthesis_model_slug = "synth"
    run.save(update_fields=["synthesis_reservation_id", "synthesis_model_slug"])
    BalanceReservation.objects.filter(pk=reservation.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )

    result = recover_stale_operations()

    run.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert result["compare_synthesis"] == 1
    assert reservation.state == BalanceReservation.State.RELEASED
    assert run.synthesis_reservation_id is None
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
