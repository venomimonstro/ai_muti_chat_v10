import json

import pytest

from .adapters import DeepSeekChatAdapter, OpenRouterChatAdapter, ProviderError, XAIChatAdapter
from .gigachat_adapter import GigaChatAPIAdapter


@pytest.mark.parametrize("adapter_type", [DeepSeekChatAdapter, XAIChatAdapter, OpenRouterChatAdapter, GigaChatAPIAdapter])
@pytest.mark.parametrize("ending", ["truncated", "missing_usage", "done", "finish_reason"])
def test_compatible_stream_requires_completion_and_confirmed_usage(monkeypatch, adapter_type, ending):
    class Response:
        status_code = 200
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield "data: " + json.dumps({"id": "request", "choices": [{"delta": {"content": "Ответ"}}]})
            if ending in {"done", "finish_reason"}:
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}] if ending == "finish_reason" else [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
            if ending in {"done", "missing_usage"}:
                yield "data: [DONE]"

    monkeypatch.setattr("apps.ai_registry.adapters.httpx.stream", lambda *args, **kwargs: Response())
    adapter = adapter_type(authorization_key="test") if adapter_type is GigaChatAPIAdapter else adapter_type(api_key="test")
    if adapter_type is GigaChatAPIAdapter:
        monkeypatch.setattr(adapter, "_headers", lambda **kwargs: {})
    stream = adapter.stream(model="model", messages=[{"role": "user", "content": "Запрос"}], max_output_tokens=100)
    assert next(stream).text_delta == "Ответ"
    if ending in {"truncated", "missing_usage"}:
        with pytest.raises(ProviderError) as error:
            list(stream)
        assert error.value.code == ("invalid_stream" if ending == "truncated" else "provider_usage_missing")
    else:
        completed, = list(stream)
        assert (completed.kind, completed.input_tokens, completed.output_tokens) == ("completed", 10, 2)


@pytest.mark.parametrize("usage", [{}, {"prompt_tokens": 10}, {"prompt_tokens": 0, "completion_tokens": 2}, {"prompt_tokens": 10, "completion_tokens": -1}, {"prompt_tokens": 10, "completion_tokens": 1.5}])
def test_invalid_usage_is_never_a_free_completed_response(usage):
    from .adapters import _chat_completion_event

    with pytest.raises(ProviderError, match="confirm token usage"):
        _chat_completion_event("request", usage, True)
