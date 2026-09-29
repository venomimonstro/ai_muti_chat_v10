import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError, ProviderStreamEvent
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderApiKey
from apps.billing.models import PriceVersion
from apps.billing.services import credit

from .managed_stream import managed_run
from .models import Conversation, Generation, Message
from .streaming import prepare


class ExhaustedChatGPTAdapter:
    def stream(self, **_kwargs):
        raise ProviderError(
            "OpenAI credits exhausted",
            code="credit_balance_exhausted",
            retryable=False,
        )
        yield


class WorkingSystemAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="Рабочий системный ответ")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="system-fallback-ok",
            input_tokens=12,
            output_tokens=5,
        )


def _key(provider, label, secret):
    key = ProviderApiKey(
        provider=provider,
        label=label,
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    )
    key.set_secret(secret)
    key.save()
    return key


def _model(provider, slug, upstream):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=upstream,
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=32768,
        max_output_tokens=2048,
    )
    version = ModelVersion.objects.create(
        model=model,
        version="v1",
        exact_api_id=upstream,
        capabilities=["text", "streaming"],
        context_window=32768,
        max_output_tokens=2048,
        stage=ModelVersion.Stage.ACTIVE,
        activated_at=timezone.now(),
    )
    model.current_version = version
    model.save(update_fields=["current_version"])
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        active=True,
        effective_from=timezone.now(),
    )
    return model


def _provider(slug, name, priority):
    provider, _ = Provider.objects.get_or_create(slug=slug, defaults={"name": name})
    provider.name = name
    provider.enabled = True
    provider.emergency_disabled = False
    provider.health_state = Provider.HealthState.HEALTHY
    provider.consecutive_failures = 0
    provider.circuit_opened_until = None
    provider.priority = priority
    provider.save(
        update_fields=[
            "name",
            "enabled",
            "emergency_disabled",
            "health_state",
            "consecutive_failures",
            "circuit_opened_until",
            "priority",
        ]
    )
    provider.api_keys.all().delete()
    AIModel.objects.filter(provider=provider).update(enabled=False)
    return provider


@pytest.mark.django_db(transaction=True)
def test_customer_gets_llm_system_answer_when_selected_chatgpt_has_no_credits(monkeypatch):
    user = User.objects.create_user(
        username="provider-fallback-user",
        email="provider-fallback@example.test",
        password="password123",
    )
    credit(user, Decimal("10"), "test", "provider-fallback")

    openai = _provider("openai", "OpenAI", 10)
    system = _provider("gigachat", "GigaChat API", 20)
    _key(openai, "fallback-openai-primary", "openai-test-key")
    _key(system, "fallback-system-primary", "system-test-key")
    chatgpt = _model(openai, "openai-test-chat", "gpt-test")
    system_model = _model(system, "gigachat-test-pro", "GigaChat-2-Pro")

    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.MANUAL,
        selected_model=chatgpt.slug,
    )
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Ответь пользователю, несмотря на сбой основного провайдера",
        client_message_id=uuid.uuid4(),
        idempotency_key="provider-exhaustion-failover",
    )
    assert created is True

    monkeypatch.setattr(
        "apps.chat.streaming.adapter_for",
        lambda model: ExhaustedChatGPTAdapter()
        if model.provider.slug == "openai"
        else WorkingSystemAdapter(),
    )
    monkeypatch.setattr(
        "apps.chat.managed_stream.adapter_for",
        lambda model: WorkingSystemAdapter()
        if model.provider.slug == "gigachat"
        else ExhaustedChatGPTAdapter(),
    )

    chunks = list(managed_run(generation))

    generation.refresh_from_db()
    generation.assistant_message.refresh_from_db()
    openai.refresh_from_db()
    system.refresh_from_db()

    assert generation.state == Generation.State.COMPLETED
    assert generation.assistant_message.status == Message.Status.COMPLETED
    assert generation.assistant_message.content == "Рабочий системный ответ"
    assert generation.provider_slug == "gigachat"
    assert generation.routed_model == system_model.slug
    assert generation.actual_cost_rub == Decimal("0")
    assert openai.health_state == Provider.HealthState.OPEN
    assert system.health_state == Provider.HealthState.HEALTHY

    public_stream = "".join(chunks)
    assert "Рабочий системный ответ" in public_stream
    assert "event: completed" in public_stream
    assert "event: error" not in public_stream
    assert "gigachat" not in public_stream.casefold()
    assert "emergency_fallback" in public_stream
