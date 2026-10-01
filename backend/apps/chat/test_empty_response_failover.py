import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from . import response_safety
from .managed_stream import managed_run
from .models import Conversation, Generation, GenerationAttempt, Message
from .streaming import prepare


class BlankCompletedAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="blank-provider-response",
            input_tokens=12,
            output_tokens=1,
        )


class WhitespaceCompletedAdapter:
    def stream(self, **_kwargs):
        # Some upstreams occasionally emit formatting-only chunks before declaring
        # completion. These chunks must not count as customer-visible output or they
        # would disable the safe cross-model fallback path.
        yield ProviderStreamEvent(kind="delta", text_delta="\n")
        yield ProviderStreamEvent(kind="delta", text_delta="   ")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="whitespace-provider-response",
            input_tokens=12,
            output_tokens=2,
        )


class WorkingFallbackAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="Нормальный резервный ответ")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="working-fallback-response",
            input_tokens=14,
            output_tokens=7,
        )


def _priced_model(provider, slug, *, fallback=None):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=True,
        fallback_model=fallback,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=1024,
    )
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


@pytest.mark.parametrize(
    "primary_adapter",
    [BlankCompletedAdapter, WhitespaceCompletedAdapter],
    ids=["empty", "whitespace-only"],
)
@pytest.mark.django_db(transaction=True)
def test_blank_completed_response_falls_back_without_poisoning_provider(monkeypatch, primary_adapter):
    assert getattr(response_safety, "_raw_adapter_for", None) is not None

    user = User.objects.create_user(
        username=f"blank-response-user-{primary_adapter.__name__}",
        email=f"blank-response-{primary_adapter.__name__}@example.test",
        password="password123",
    )
    credit(user, Decimal("20"), "test", f"blank-response-fallback-{primary_adapter.__name__}")
    AIModel.objects.all().update(enabled=False)

    provider = Provider.objects.create(
        slug=f"blank-response-echo-{primary_adapter.__name__.lower()}",
        name="Blank response Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
        priority=1,
    )
    fallback = _priced_model(provider, f"blank-response-fallback-{primary_adapter.__name__.lower()}")
    primary = _priced_model(
        provider,
        f"blank-response-primary-{primary_adapter.__name__.lower()}",
        fallback=fallback,
    )

    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.MANUAL,
        selected_model=primary.slug,
    )
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Не возвращай пользователю пустой успешный ответ",
        client_message_id=uuid.uuid4(),
        idempotency_key=f"blank-response-fallback:{primary_adapter.__name__}:request",
    )
    assert created is True

    calls = []

    def fake_adapter_for(model, *_args, **_kwargs):
        calls.append(model.slug)
        if model.slug == primary.slug:
            return primary_adapter()
        return WorkingFallbackAdapter()

    monkeypatch.setattr(response_safety, "_raw_adapter_for", fake_adapter_for)

    body = "".join(managed_run(generation))

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    provider.refresh_from_db()
    user.wallet.refresh_from_db()
    reservation = BalanceReservation.objects.get(pk=generation.reservation_id)
    request_cost = RequestCost.objects.get(generation_id=generation.id)
    attempts = list(generation.attempts.order_by("sequence"))

    assert calls[:2] == [primary.slug, fallback.slug]
    assert "event: completed" in body
    assert "event: error" not in body
    assert "Нормальный резервный ответ" in body
    assert generation.state == Generation.State.COMPLETED
    assert generation.routed_model == fallback.slug
    assert generation.assistant_message.status == Message.Status.COMPLETED
    assert generation.assistant_message.content == "Нормальный резервный ответ"
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert len(attempts) == 2
    assert attempts[0].model_slug == primary.slug
    assert attempts[0].state == GenerationAttempt.State.FAILED
    assert attempts[0].error_code == response_safety.EMPTY_RESPONSE_CODE
    assert attempts[1].model_slug == fallback.slug
    assert attempts[1].state == GenerationAttempt.State.COMPLETED
    assert reservation.state == BalanceReservation.State.SETTLED
    assert request_cost.charged_rub == generation.actual_cost_rub
    assert user.wallet.reserved_rub == Decimal("0.0000")
