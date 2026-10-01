import json
import uuid
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion
from apps.billing.services import credit, settle

from .cancellation import request_cancel
from .cooperative_cancel import _cancelled_event
from .models import Conversation, Generation, Message
from .streaming import prepare, run


class MustNotRunAdapter:
    def stream(self, **_kwargs):
        raise AssertionError("provider must not be called after cancellation")
        yield  # pragma: no cover


class MidStreamAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="частичный ответ")
        yield ProviderStreamEvent(kind="delta", text_delta=" который уже не должен прийти")
        yield ProviderStreamEvent(
            kind="completed",
            input_tokens=24,
            output_tokens=12,
            provider_request_id="mid-stream-cancel",
        )


def _fixture():
    user = User.objects.create_user(
        username=f"cancel-{uuid.uuid4()}",
        email=f"cancel-{uuid.uuid4()}@example.test",
        password="password123!",
    )
    credit(user, Decimal("20"), "test", f"cancel-credit-{uuid.uuid4()}")
    provider = Provider.objects.create(
        slug=f"cancel-echo-{uuid.uuid4()}",
        name="Cancel Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug=f"cancel-model-{uuid.uuid4()}",
        display_name="Cancel model",
        upstream_model="cancel-model",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=1024,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("1"),
        output_rub_per_million=Decimal("2"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="Cancelable",
        selected_model=model.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    key = f"cancel:{uuid.uuid4()}"
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Останови этот запрос",
        client_message_id=uuid.uuid4(),
        idempotency_key=key,
    )
    assert created is True
    return user, conversation, generation, key


@pytest.mark.django_db(transaction=True)
def test_stop_before_provider_call_cancels_without_external_request_or_charge():
    user, conversation, generation, key = _fixture()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/cancel/",
        {"idempotency_key": key},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 202

    body = "".join(run(generation, adapter=MustNotRunAdapter()))

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()

    assert "event: cancelled" in body
    assert "client_cancelled" in body
    assert generation.state == Generation.State.CANCELLED
    assert generation.error_code == "client_cancelled"
    assert generation.actual_cost_rub == Decimal("0.0000")
    assert generation.assistant_message.status == Message.Status.FAILED
    assert reservation.state == BalanceReservation.State.RELEASED
    assert reservation.actual_rub == Decimal("0.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("20.0000")


@pytest.mark.django_db(transaction=True)
def test_stop_after_first_delta_is_durable_cancel_not_stream_failure():
    user, _conversation, generation, key = _fixture()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    chunks = []
    cancel_sent = False

    for chunk in run(generation, adapter=MidStreamAdapter()):
        chunks.append(chunk)
        if not cancel_sent and chunk.startswith("event: delta\n"):
            request_cancel(
                owner_id=user.id,
                idempotency_key=key,
                generation_id=generation.id,
            )
            cancel_sent = True

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    body = "".join(chunks)

    assert cancel_sent is True
    assert "event: cancelled" in body
    assert generation.state == Generation.State.CANCELLED
    assert generation.error_code == "client_cancelled"
    assert generation.assistant_message.status == Message.Status.PARTIAL
    assert generation.assistant_message.content == "частичный ответ"
    assert reservation.state == BalanceReservation.State.RELEASED
    assert reservation.actual_rub == Decimal("0.0000")
    assert generation.actual_cost_rub == Decimal("0.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert "stream_incomplete" not in body


@pytest.mark.django_db(transaction=True)
def test_cancelled_event_uses_authoritative_settled_reservation_cost():
    _user, _conversation, generation, _key = _fixture()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    charge = min(Decimal("0.0100"), reservation.amount_rub)
    assert charge > 0
    settle(reservation.id, charge)

    assistant = generation.assistant_message
    assistant.content = "частичный ответ"
    assistant.status = Message.Status.PARTIAL
    assistant.save(update_fields=["content", "status"])
    generation.state = Generation.State.CANCELLED
    generation.error_code = "client_cancelled"
    generation.actual_cost_rub = Decimal("0")
    generation.completed_at = timezone.now()
    generation.save(update_fields=["state", "error_code", "actual_cost_rub", "completed_at"])

    chunk = _cancelled_event(generation)
    payload = json.loads(next(line[6:] for line in chunk.splitlines() if line.startswith("data: ")))

    generation.refresh_from_db()
    assert generation.actual_cost_rub == charge
    assert Decimal(str(payload["cost_rub"])) == charge
    assert "подтверждённая стоимость" in payload["message"].lower()


@pytest.mark.django_db(transaction=True)
def test_user_cannot_cancel_another_users_generation():
    owner, conversation, generation, key = _fixture()
    stranger = User.objects.create_user(
        username=f"stranger-{uuid.uuid4()}",
        email=f"stranger-{uuid.uuid4()}@example.test",
        password="password123!",
    )
    client = APIClient()
    client.force_authenticate(stranger)

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/cancel/",
        {"idempotency_key": key},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )

    assert response.status_code == 404
    generation.refresh_from_db()
    assert generation.state == Generation.State.QUEUED
