import json

import pytest

from .adapters import OpenAIResponsesAdapter


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
