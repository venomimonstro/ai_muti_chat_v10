import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit, release, reserve

from . import provider_delivery_checkpoint
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_provider_delivery_checkpoint_prevents_false_full_refund():
    user = User.objects.create_user(
        username="delivery-checkpoint-user",
        email="delivery-checkpoint@example.test",
        password="password123!",
    )
    credit(user, Decimal("100"), "test", "delivery-checkpoint")
    generation_id = uuid.uuid4()
    reservation = reserve(user, Decimal("5"), f"generation:{generation_id}")
    price = PriceVersion.objects.create(
        model_slug="delivery-checkpoint-model",
        input_rub_per_million=Decimal("1000000"),
        output_rub_per_million=Decimal("1000000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    request_cost = RequestCost.objects.create(
        generation_id=generation_id,
        price_version=price,
        estimated_rub=Decimal("5"),
    )
    model = SimpleNamespace(slug=price.model_slug)
    completed = SimpleNamespace(input_tokens=10, output_tokens=10)

    token = provider_delivery_checkpoint._CURRENT_GENERATION_ID.set(generation_id)
    try:
        provider_delivery_checkpoint._checkpoint(model, completed)
    finally:
        provider_delivery_checkpoint._CURRENT_GENERATION_ID.reset(token)

    request_cost.refresh_from_db()
    assert request_cost.provider_cost_rub == Decimal("20.0000")
    assert request_cost.input_tokens == 10
    assert request_cost.output_tokens == 10

    # Simulate the ordinary customer-settlement path failing after provider delivery.
    # The generic failure handler calls release(); durable provider usage must make
    # release settle at the already authorized ceiling rather than refunding 100%.
    closed = release(reservation.id)
    closed.refresh_from_db()
    request_cost.refresh_from_db()
    user.wallet.refresh_from_db()

    assert closed.state == BalanceReservation.State.SETTLED
    assert closed.actual_rub == Decimal("5.0000")
    assert request_cost.charged_rub == Decimal("5.0000")
    assert user.wallet.available_rub == Decimal("95.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_provider_delivery_checkpoint_persists_short_answer_before_terminal_commit():
    user = User.objects.create_user(
        username="delivery-text-user",
        email="delivery-text@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Crash safe answer")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Короткий вопрос",
        client_message_id=uuid.uuid4(),
        status=Message.Status.SAVED,
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="",
        status=Message.Status.SAVED,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        model="delivery-text-model",
        idempotency_key="delivery-text-checkpoint",
        state=Generation.State.RUNNING,
    )
    price = PriceVersion.objects.create(
        model_slug="delivery-text-model",
        input_rub_per_million=Decimal("1.0000"),
        output_rub_per_million=Decimal("1.0000"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    RequestCost.objects.create(
        generation_id=generation.id,
        price_version=price,
        estimated_rub=Decimal("1.0000"),
    )
    model = SimpleNamespace(slug=price.model_slug)
    completed = SimpleNamespace(input_tokens=12, output_tokens=4)

    token = provider_delivery_checkpoint._CURRENT_GENERATION_ID.set(generation.id)
    try:
        provider_delivery_checkpoint._checkpoint(
            model,
            completed,
            delivered_text="Короткий готовый ответ",
        )
    finally:
        provider_delivery_checkpoint._CURRENT_GENERATION_ID.reset(token)

    assistant.refresh_from_db()
    request_cost = RequestCost.objects.get(generation_id=generation.id)
    assert assistant.content == "Короткий готовый ответ"
    assert assistant.status == Message.Status.STREAMING
    assert request_cost.input_tokens == 12
    assert request_cost.output_tokens == 4
    assert request_cost.provider_cost_rub is not None
