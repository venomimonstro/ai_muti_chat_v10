from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve
from apps.chat.models import Conversation, Generation, Message
from apps.chat.paid_search_billing import public_search_charge


def _generation(user, suffix: str):
    conversation = Conversation.objects.create(owner=user, title=f"Search {suffix}")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Найди актуальную информацию",
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
    )
    return Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant_message,
        model="echo-v1",
        idempotency_key=f"search-billing-{suffix}",
    )


@pytest.mark.django_db(transaction=True)
def test_paid_search_customer_charge_is_settled_only_for_completed_answer():
    user = User.objects.create_user(
        username="paid-search-completed",
        email="paid-search-completed@example.test",
        password="password123",
    )
    credit(user, Decimal("20"), "test", "paid-search-completed")
    generation = _generation(user, "completed")
    reservation = reserve(user, Decimal("2.50"), f"web-search:{generation.id}")

    assert public_search_charge(generation) == Decimal("0")
    generation.state = Generation.State.COMPLETED
    generation.save(update_fields=["state"])

    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.SETTLED
    assert reservation.actual_rub == Decimal("2.5000")
    assert public_search_charge(generation) == Decimal("2.5000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_paid_search_customer_charge_is_refunded_when_answer_fails():
    user = User.objects.create_user(
        username="paid-search-failed",
        email="paid-search-failed@example.test",
        password="password123",
    )
    credit(user, Decimal("20"), "test", "paid-search-failed")
    generation = _generation(user, "failed")
    reservation = reserve(user, Decimal("2.50"), f"web-search:{generation.id}")

    generation.state = Generation.State.FAILED
    generation.save(update_fields=["state"])

    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.RELEASED
    assert reservation.actual_rub is None
    assert public_search_charge(generation) == Decimal("0")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("20.0000")
