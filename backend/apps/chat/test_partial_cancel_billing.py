from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from .managed_stream import managed_run
from .models import Conversation, Generation, Message
from .streaming import prepare


class PartialStreamAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="частичный полезный ответ " * 20)
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="partial-complete",
            input_tokens=50,
            output_tokens=200,
        )


@pytest.fixture
def cancellation_context():
    user = User.objects.create_user(
        username="cancel-billing",
        email="cancel-billing@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "cancel-billing")
    provider = Provider.objects.create(
        slug="cancel-echo",
        name="Cancel Echo",
        adapter_type=Provider.AdapterType.ECHO,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="cancel-echo-v1",
        display_name="Cancel Echo",
        upstream_model="cancel-echo-v1",
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("1000"),
        output_rub_per_million=Decimal("2000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(
        owner=user,
        selected_model=model.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    return user, conversation


@pytest.mark.django_db(transaction=True)
def test_cancel_after_delivered_delta_charges_only_partial_and_releases_reserve(cancellation_context):
    user, conversation = cancellation_context
    before = user.wallet.available_rub
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Дай длинный ответ",
        client_message_id="6933eb8c-0d14-4ad3-a2e2-1ab05a796501",
        idempotency_key="cancel:after-delta",
    )
    assert created is True
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    reserved_max = reservation.amount_rub

    stream = managed_run(generation, adapter=PartialStreamAdapter())
    assert "event: generation" in next(stream)
    delta = next(stream)
    assert "event: delta" in delta
    stream.close()

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()
    reservation.refresh_from_db()
    request_cost = RequestCost.objects.get(generation_id=generation.id)

    assert generation.state == Generation.State.CANCELLED
    assert generation.assistant_message.status == Message.Status.PARTIAL
    assert generation.assistant_message.content
    assert generation.actual_cost_rub is not None
    assert Decimal("0") < generation.actual_cost_rub <= reserved_max
    assert request_cost.charged_rub == generation.actual_cost_rub
    assert reservation.state == BalanceReservation.State.SETTLED
    assert reservation.actual_rub == generation.actual_cost_rub
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert before - user.wallet.available_rub == generation.actual_cost_rub


@pytest.mark.django_db(transaction=True)
def test_cancel_before_first_provider_delta_costs_zero(cancellation_context):
    user, conversation = cancellation_context
    before = user.wallet.available_rub
    generation, _created = prepare(
        user=user,
        conversation=conversation,
        content="Начни ответ",
        client_message_id="30c6530c-7715-4dfd-8fa6-d1fbcc497d7f",
        idempotency_key="cancel:before-delta",
    )

    stream = managed_run(generation, adapter=PartialStreamAdapter())
    assert "event: generation" in next(stream)
    stream.close()

    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    request_cost = RequestCost.objects.get(generation_id=generation.id)

    assert generation.state == Generation.State.CANCELLED
    assert generation.actual_cost_rub == Decimal("0.0000")
    assert request_cost.charged_rub is None
    assert reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.available_rub == before
    assert user.wallet.reserved_rub == Decimal("0.0000")
