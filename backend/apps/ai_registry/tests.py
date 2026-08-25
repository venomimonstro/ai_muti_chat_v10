import json

import pytest

from .adapters import (
    AnthropicMessagesAdapter,
    DeepSeekChatAdapter,
    GeminiGenerateContentAdapter,
    OpenAIResponsesAdapter,
    ProviderError,
    XAIChatAdapter,
    adapter_for,
)
from .models import AIModel, Provider
from .reliability import provider_available, record_failure, record_success


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self):
        events = [
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "При"},
            {"type": "response.output_text.delta", "delta": "вет"},
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_test",
                    "usage": {"input_tokens": 11, "output_tokens": 2},
                },
            },
        ]
        return [f"data: {json.dumps(event)}" for event in events]


@pytest.mark.django_db
def test_openai_responses_adapter_parses_typed_sse(monkeypatch):
    monkeypatch.setattr("apps.ai_registry.adapters.httpx.stream", lambda *a, **k: FakeResponse())
    adapter = OpenAIResponsesAdapter(api_key="test")
    result = adapter.generate(
        model="test-model",
        messages=[{"role": "user", "content": "Привет"}],
        max_output_tokens=100,
    )
    assert result.text == "Привет"
    assert result.provider_request_id == "resp_test"
    assert result.input_tokens == 11
    assert result.output_tokens == 2


class AnthropicResponse(FakeResponse):
    def iter_lines(self):
        events = [
            {
                "type": "message_start",
                "message": {"id": "msg_test", "usage": {"input_tokens": 7}},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Claude"},
            },
            {"type": "message_delta", "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
        return [f"data: {json.dumps(event)}" for event in events]


@pytest.mark.django_db
def test_anthropic_adapter_normalizes_stream(monkeypatch):
    monkeypatch.setattr(
        "apps.ai_registry.adapters.httpx.stream", lambda *a, **k: AnthropicResponse()
    )
    result = AnthropicMessagesAdapter(api_key="test").generate(
        model="claude-test",
        messages=[
            {"role": "system", "content": "Rules"},
            {"role": "user", "content": "Hello"},
        ],
        max_output_tokens=100,
    )
    assert result.text == "Claude"
    assert result.provider_request_id == "msg_test"
    assert result.input_tokens == 7
    assert result.output_tokens == 3


class DeepSeekResponse(FakeResponse):
    def iter_lines(self):
        events = [
            {"id": "deep_test", "choices": [{"delta": {"content": "Deep"}}]},
            {
                "id": "deep_test",
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        ]
        return [*[f"data: {json.dumps(event)}" for event in events], "data: [DONE]"]


@pytest.mark.django_db
def test_deepseek_adapter_normalizes_openai_compatible_stream(monkeypatch):
    monkeypatch.setattr(
        "apps.ai_registry.adapters.httpx.stream", lambda *a, **k: DeepSeekResponse()
    )
    result = DeepSeekChatAdapter(api_key="test").generate(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "Hello"}],
        max_output_tokens=100,
    )
    assert result.text == "Deep"
    assert result.provider_request_id == "deep_test"
    assert result.input_tokens == 5
    assert result.output_tokens == 2


class GeminiResponse(FakeResponse):
    def iter_lines(self):
        events = [
            {
                "responseId": "gemini_test",
                "candidates": [{"content": {"parts": [{"text": "Gem"}]}}],
            },
            {
                "responseId": "gemini_test",
                "candidates": [{"content": {"parts": [{"text": "ini"}]}}],
                "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 2},
            },
        ]
        return [f"data: {json.dumps(event)}" for event in events]


def test_gemini_adapter_normalizes_sse_and_usage(monkeypatch):
    monkeypatch.setattr(
        "apps.ai_registry.adapters.httpx.stream", lambda *a, **k: GeminiResponse()
    )
    result = GeminiGenerateContentAdapter(api_key="test").generate(
        model="gemini-test",
        messages=[
            {"role": "system", "content": "Rules"},
            {"role": "user", "content": "Hello"},
        ],
        max_output_tokens=100,
    )
    assert result.text == "Gemini"
    assert result.provider_request_id == "gemini_test"
    assert result.input_tokens == 6
    assert result.output_tokens == 2


def test_xai_adapter_uses_openai_compatible_usage_contract(monkeypatch):
    monkeypatch.setattr(
        "apps.ai_registry.adapters.httpx.stream", lambda *a, **k: DeepSeekResponse()
    )
    result = XAIChatAdapter(api_key="test").generate(
        model="grok-test",
        messages=[{"role": "user", "content": "Hello"}],
        max_output_tokens=100,
    )
    assert result.text == "Deep"
    assert result.input_tokens == 5
    assert result.output_tokens == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("adapter_type", "expected_type", "credential"),
    [
        (Provider.AdapterType.OPENAI_RESPONSES, OpenAIResponsesAdapter, "OPENAI_API_KEY"),
        (Provider.AdapterType.ANTHROPIC_MESSAGES, AnthropicMessagesAdapter, "ANTHROPIC_API_KEY"),
        (Provider.AdapterType.DEEPSEEK_CHAT, DeepSeekChatAdapter, "DEEPSEEK_API_KEY"),
        (
            Provider.AdapterType.GEMINI_GENERATE_CONTENT,
            GeminiGenerateContentAdapter,
            "GEMINI_API_KEY",
        ),
        (Provider.AdapterType.XAI_CHAT, XAIChatAdapter, "XAI_API_KEY"),
    ],
)
def test_five_provider_families_share_adapter_contract(
    monkeypatch, adapter_type, expected_type, credential
):
    monkeypatch.setenv(credential, "server-secret")
    provider = Provider.objects.create(
        slug=f"provider-{adapter_type}", name=adapter_type, adapter_type=adapter_type
    )
    model = AIModel.objects.create(
        provider=provider,
        slug=f"model-{adapter_type}",
        display_name=adapter_type,
        upstream_model="exact-model-id",
    )
    adapter = adapter_for(model)
    assert isinstance(adapter, expected_type)
    assert {"text", "streaming"}.issubset(adapter.capabilities())


@pytest.mark.django_db(transaction=True)
def test_circuit_opens_and_recovers(settings):
    settings.AI_CIRCUIT_FAILURE_THRESHOLD = 2
    provider = Provider.objects.create(slug="unstable", name="Unstable")
    error = ProviderError("timeout", code="timeout", retryable=True)
    record_failure(provider, error)
    record_failure(provider, error)
    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.OPEN
    assert provider_available(provider) is False
    record_success(provider, 25)
    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert provider.consecutive_failures == 0
    assert provider_available(provider) is True


@pytest.mark.django_db
def test_emergency_kill_switch_blocks_provider():
    provider = Provider.objects.create(
        slug="disabled", name="Disabled", emergency_disabled=True
    )
    assert provider_available(provider) is False
