import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from .models import Conversation, Generation, Message
from .streaming import prepare, run


class ConfirmedOverrunAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="Полный полезный ответ пользователю.")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="confirmed-overrun-provider-request",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )


@pytest.mark.django_db(transaction=True)
def test_completed_provider_response_is_not_lost_when_usage_exceeds_reserve(monkeypatch):
    assert getattr(run, "_ai_workspace_terminal_recovery", False) is True
    indexed = []
    summaries = []
    monkeypatch.setattr(
        "apps.chat.streaming._index_history",
        lambda message: indexed.append(str(message.id)),
    )
    monkeypatch.setattr(
        "apps.chat.streaming.refresh_rolling_summary",
        lambda conversation: summaries.append(str(conversation.id)),
    )

    user = User.objects.create_user(
        username="terminal-overrun-user",
        email="terminal-overrun@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "terminal-overrun")
    provider = Provider.objects.create(
        slug="terminal-overrun-echo",
        name="Terminal overrun Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="terminal-overrun-model",
        display_name="Terminal overrun model",
        upstream_model="terminal-overrun-model",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(
        owner=user,
        selected_model=model.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Верни полноценный ответ",
        client_message_id=uuid.uuid4(),
        idempotency_key="terminal-overrun:request",
    )
    assert created is True
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    reserved = reservation.amount_rub
    assert reserved < Decimal("60")

    body = "".join(run(generation, adapter=ConfirmedOverrunAdapter()))

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    reservation.refresh_from_db()
    request_cost = RequestCost.objects.get(generation_id=generation.id)
    user.wallet.refresh_from_db()

    assert "event: completed" in body
    assert "event: error" not in body
    assert "authorized_reserve_overrun" in body
    assert generation.state == Generation.State.COMPLETED
    assert generation.error_code == ""
    assert generation.assistant_message.status == Message.Status.COMPLETED
    assert generation.assistant_message.content == "Полный полезный ответ пользователю."
    assert generation.provider_request_id == "confirmed-overrun-provider-request"
    assert reservation.state == BalanceReservation.State.SETTLED
    assert reservation.actual_rub == reserved
    assert generation.actual_cost_rub == reserved
    assert request_cost.charged_rub == reserved
    assert request_cost.provider_cost_rub is not None
    assert request_cost.input_tokens == 1_000_000
    assert request_cost.output_tokens == 1_000_000
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("100.0000") - reserved
    assert indexed == [str(generation.assistant_message_id)]
    assert summaries == [str(conversation.id)]
