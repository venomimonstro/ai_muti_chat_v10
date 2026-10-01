import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from .models import Conversation, Generation, Message


def _held_generation(user, conversation, *, amount=Decimal("5.0000")):
    client_message_id = uuid.uuid4()
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Продолжи тот же запрос после подтверждения цены",
        client_message_id=client_message_id,
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.SAVED,
    )
    reservation = reserve(user, amount, f"held-confirmation:{uuid.uuid4()}")
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.QUEUED,
        model="held-model",
        routed_model="held-model",
        idempotency_key="held-confirmation:request",
        reservation_id=reservation.id,
        error_code="cost_confirmation_changed",
        context_snapshot={"attached_files": [], "vision_assets": []},
    )
    return generation, reservation, client_message_id


@pytest.mark.django_db(transaction=True)
def test_confirmed_retry_resumes_same_queued_generation_without_new_message(monkeypatch):
    user = User.objects.create_user(
        username="held-confirmation-user",
        email="held-confirmation@example.test",
        password="password123",
    )
    credit(user, Decimal("20"), "test", "held-confirmation")
    conversation = Conversation.objects.create(owner=user, title="Held confirmation")
    generation, reservation, client_message_id = _held_generation(user, conversation)
    calls = []

    def fake_customer_stream(_request, current, *, created):
        calls.append((str(current.id), created))
        return iter([
            'event: completed\ndata: {"state":"completed","cost_rub":"0"}\n\n'
        ])

    monkeypatch.setattr("apps.chat.cost_views._customer_stream", fake_customer_stream)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": generation.user_message.content,
            "client_message_id": str(client_message_id),
            "confirm_cost": True,
            "confirmed_max_rub": "5.0000",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=generation.idempotency_key,
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    generation.refresh_from_db()
    reservation.refresh_from_db()

    assert "event: completed" in body
    assert calls == [(str(generation.id), False)]
    assert Generation.objects.filter(owner=user).count() == 1
    assert Message.objects.filter(conversation=conversation, role=Message.Role.USER).count() == 1
    assert generation.state == Generation.State.QUEUED
    assert generation.error_code == ""
    assert reservation.state == BalanceReservation.State.ACTIVE


@pytest.mark.django_db(transaction=True)
def test_retry_below_held_ceiling_stays_409_and_does_not_start_stream(monkeypatch):
    user = User.objects.create_user(
        username="held-confirmation-low-user",
        email="held-confirmation-low@example.test",
        password="password123",
    )
    credit(user, Decimal("20"), "test", "held-confirmation-low")
    conversation = Conversation.objects.create(owner=user, title="Held confirmation low")
    generation, reservation, client_message_id = _held_generation(user, conversation)

    def must_not_stream(*_args, **_kwargs):
        raise AssertionError("provider stream must not start before the exact held reserve is confirmed")

    monkeypatch.setattr("apps.chat.cost_views._customer_stream", must_not_stream)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": generation.user_message.content,
            "client_message_id": str(client_message_id),
            "confirm_cost": True,
            "confirmed_max_rub": "4.9999",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=generation.idempotency_key,
    )

    assert response.status_code == 409
    payload = response.json()
    generation.refresh_from_db()
    reservation.refresh_from_db()

    assert payload["code"] == "cost_confirmation_changed"
    assert Decimal(payload["estimated_max_rub"]) == reservation.amount_rub
    assert generation.state == Generation.State.QUEUED
    assert generation.error_code == "cost_confirmation_changed"
    assert reservation.state == BalanceReservation.State.ACTIVE
