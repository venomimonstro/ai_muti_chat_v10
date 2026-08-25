import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError, ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion, RequestCost
from apps.billing.services import credit, reconstruct

from .models import Conversation, Generation, Message
from .streaming import prepare, run


class PartialFailureAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="частичный ответ")
        raise ProviderError("timeout")


class ImpossibleUsageAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="bad-usage",
            input_tokens=10_000_000,
            output_tokens=10_000_000,
        )


class ImmediateFailureAdapter:
    def stream(self, **_kwargs):
        raise ProviderError("down", code="upstream_down", retryable=True)
        yield


class SuccessfulFallbackAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="резервный ответ")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="fallback-ok",
            input_tokens=4,
            output_tokens=3,
        )


class LongRunningAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="начало ответа")
        yield ProviderStreamEvent(kind="delta", text_delta=" продолжение")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="too-late",
            input_tokens=4,
            output_tokens=3,
        )


@pytest.fixture
def stream_context():
    user = User.objects.create_user(
        username="stream", email="stream@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "stream")
    provider = Provider.objects.create(slug="echo", name="Echo")
    AIModel.objects.create(
        provider=provider,
        slug="echo-v1",
        display_name="Echo",
        upstream_model="echo-v1",
    )
    PriceVersion.objects.create(
        model_slug="echo-v1",
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return user, Conversation.objects.create(owner=user)


@pytest.mark.django_db(transaction=True)
def test_stream_settles_actual_usage_and_persists_response(stream_context):
    user, conversation = stream_context
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Проверка",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:one",
    )
    events = "".join(run(generation))
    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()
    cost = RequestCost.objects.get(generation_id=generation.id)
    assert created is True
    assert generation.state == Generation.State.COMPLETED
    assert generation.assistant_message.status == Message.Status.COMPLETED
    generation.user_message.refresh_from_db()
    assert len(generation.user_message.embedding) == 384
    assert len(generation.assistant_message.embedding) == 384
    assert generation.user_message.embedding_model == "local-history-hash-v1"
    assert generation.assistant_message.content_sha256
    assert "event: delta" in events
    assert "event: completed" in events
    assert cost.charged_rub == generation.actual_cost_rub
    assert cost.fx_snapshot is not None
    assert cost.pricing_snapshot["fx_rate"] == "1.00000000"
    assert cost.gross_profit_rub is not None
    assert cost.gross_margin_percent == Decimal("50.000")
    assert reconstruct(user.wallet) == (user.wallet.available_rub, user.wallet.reserved_rub)


@pytest.mark.django_db(transaction=True)
def test_duplicate_stream_prepare_is_idempotent(stream_context):
    user, conversation = stream_context
    values = dict(
        user=user,
        conversation=conversation,
        content="Только один раз",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:same",
    )
    first, first_created = prepare(**values)
    second, second_created = prepare(**values)
    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert conversation.messages.count() == 2
    assert RequestCost.objects.filter(generation_id=first.id).count() == 1


@pytest.mark.django_db(transaction=True)
def test_partial_provider_failure_releases_full_reserve(stream_context):
    user, conversation = stream_context
    generation, _ = prepare(
        user=user,
        conversation=conversation,
        content="Сбой",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:fail",
    )
    events = "".join(run(generation, adapter=PartialFailureAdapter()))
    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == Generation.State.FAILED
    assert generation.assistant_message.status == Message.Status.PARTIAL
    assert generation.assistant_message.content == "частичный ответ"
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert "event: error" in events


@pytest.mark.django_db(transaction=True)
def test_client_disconnect_cancels_generation_and_releases_reserve(stream_context):
    user, conversation = stream_context
    generation, _ = prepare(
        user=user,
        conversation=conversation,
        content="Остановить ответ",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:cancel",
    )

    stream = run(generation, adapter=LongRunningAdapter())
    assert "event: generation" in next(stream)
    assert "event: delta" in next(stream)
    stream.close()

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == Generation.State.CANCELLED
    assert generation.error_code == "client_cancelled"
    assert generation.assistant_message.status == Message.Status.PARTIAL
    assert generation.assistant_message.content == "начало ответа"
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_usage_above_reserved_maximum_is_not_debited(stream_context):
    user, conversation = stream_context
    generation, _ = prepare(
        user=user,
        conversation=conversation,
        content="Аномальный usage",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:anomaly",
    )
    events = "".join(run(generation, adapter=ImpossibleUsageAdapter()))
    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "cost_or_internal_error"
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert "event: error" in events


@pytest.mark.django_db(transaction=True)
def test_retry_then_fallback_records_attempts(monkeypatch, settings):
    settings.AI_PROVIDER_MAX_ATTEMPTS = 2
    user = User.objects.create_user(
        username="fallback", email="fallback@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "fallback")
    primary_provider = Provider.objects.create(
        slug="primary", name="Gemini", adapter_type=Provider.AdapterType.GEMINI_GENERATE_CONTENT
    )
    fallback_provider = Provider.objects.create(
        slug="fallback", name="Grok", adapter_type=Provider.AdapterType.XAI_CHAT
    )
    fallback_model = AIModel.objects.create(
        provider=fallback_provider,
        slug="fallback-v1",
        display_name="Fallback",
        upstream_model="fallback-v1",
    )
    primary_model = AIModel.objects.create(
        provider=primary_provider,
        slug="primary-v1",
        display_name="Primary",
        upstream_model="primary-v1",
        fallback_model=fallback_model,
    )
    for model in (primary_model, fallback_model):
        PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=Decimal("10"),
            output_rub_per_million=Decimal("20"),
            markup_percent=Decimal("100"),
            effective_from=timezone.now(),
        )
    conversation = Conversation.objects.create(owner=user, selected_model=primary_model.slug)
    generation, _ = prepare(
        user=user,
        conversation=conversation,
        content="Fallback",
        client_message_id=uuid.uuid4(),
        idempotency_key="stream:fallback",
    )
    monkeypatch.setattr(
        "apps.chat.streaming.adapter_for",
        lambda model: ImmediateFailureAdapter()
        if model.slug == primary_model.slug
        else SuccessfulFallbackAdapter(),
    )
    events = "".join(run(generation))
    generation.refresh_from_db()
    assert generation.state == Generation.State.COMPLETED
    assert generation.routed_model == fallback_model.slug
    assert generation.provider_slug == fallback_provider.slug
    assert generation.attempts.count() == 3
    assert '"action": "retry"' in events
    assert '"action": "fallback"' in events
