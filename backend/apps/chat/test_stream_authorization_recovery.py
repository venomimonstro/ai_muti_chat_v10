from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from . import cost_views
from .models import Conversation, Generation, Message


def _prepared_generation(suffix):
    user = User.objects.create_user(
        username=f"auth-recovery-{suffix}",
        email=f"auth-recovery-{suffix}@example.test",
        password="password123!",
    )
    credit(user, Decimal("10"), "test", f"auth-recovery-{suffix}")
    conversation = Conversation.objects.create(owner=user)
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Проверка authorization recovery",
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.STREAMING,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.QUEUED,
        model="test-model",
        idempotency_key=f"auth-recovery:{suffix}",
    )
    reservation = reserve(
        user,
        Decimal("1.0000"),
        f"generation:{generation.id}",
    )
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    return user, generation, reservation


@pytest.mark.django_db(transaction=True)
def test_authorization_failure_terminalizes_turn_and_refunds_customer_reserve():
    user, generation, reservation = _prepared_generation("refund")

    terminal = cost_views._terminalize_stream_authorization_failure(
        generation,
        RuntimeError("authorization write failed"),
    )

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()

    assert terminal.state == Generation.State.FAILED
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "stream_authorization_failed"
    assert generation.completed_at is not None
    assert generation.assistant_message.status == Message.Status.FAILED
    assert reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_refund_failure_cannot_roll_back_terminal_authorization_state():
    _user, generation, reservation = _prepared_generation("refund-failure")

    with patch(
        "apps.chat.cost_views.release",
        side_effect=RuntimeError("synthetic refund database failure"),
    ):
        terminal = cost_views._terminalize_stream_authorization_failure(
            generation,
            RuntimeError("authorization write failed"),
        )

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    reservation.refresh_from_db()

    assert terminal.state == Generation.State.FAILED
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "stream_authorization_failed"
    assert generation.assistant_message.status == Message.Status.FAILED
    # Recovery can retry this idempotent ACTIVE refund later; provider execution is
    # permanently fenced by the durable FAILED generation.
    assert reservation.state == BalanceReservation.State.ACTIVE

    from apps.admin_ops.recovery import recover_terminal_chat_reservations

    result = recover_terminal_chat_reservations(older_than_seconds=0)
    reservation.refresh_from_db()

    assert result["released"] == 1
    assert reservation.state == BalanceReservation.State.RELEASED
