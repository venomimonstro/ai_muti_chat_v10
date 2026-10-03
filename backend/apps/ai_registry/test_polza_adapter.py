from contextlib import contextmanager
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from .adapters import PolzaChatAdapter


class PolzaAdapterTests(SimpleTestCase):
    def test_health_uses_models_endpoint_with_bearer_key(self):
        adapter = PolzaChatAdapter(
            api_key="pza_test",
            base_url="https://polza.ai/api/v1",
        )
        response = httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "openai/gpt-6-luna"}]},
            request=httpx.Request("GET", "https://polza.ai/api/v1/models"),
        )
        with patch("apps.ai_registry.adapters.httpx.get", return_value=response) as mocked:
            health = adapter.health_check()

        self.assertTrue(health.healthy)
        args, kwargs = mocked.call_args
        self.assertEqual(args[0], "https://polza.ai/api/v1/models")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer pza_test")

    def test_polza_keeps_openai_compatible_chat_endpoint(self):
        adapter = PolzaChatAdapter(
            api_key="pza_test",
            base_url="https://polza.ai/api/v1/",
        )
        self.assertEqual(adapter.base_url, "https://polza.ai/api/v1")
        self.assertEqual(adapter.headers["Authorization"], "Bearer pza_test")


    def test_stream_maps_polza_payment_required_to_balance_exhausted(self):
        adapter = PolzaChatAdapter(
            api_key="pza_test",
            base_url="https://polza.ai/api/v1",
        )

        @contextmanager
        def fake_stream(*args, **kwargs):
            response = httpx.Response(
                402,
                json={"error": {"code": "402", "message": "insufficient balance"}},
                request=httpx.Request(
                    "POST",
                    "https://polza.ai/api/v1/chat/completions",
                ),
            )
            yield response

        with patch("apps.ai_registry.adapters.httpx.stream", fake_stream):
            with self.assertRaises(Exception) as caught:
                list(
                    adapter.stream(
                        model="openai/gpt-6-luna",
                        messages=[{"role": "user", "content": "test"}],
                        max_output_tokens=16,
                    )
                )

        self.assertEqual(getattr(caught.exception, "code", ""), "credit_balance_exhausted")
        self.assertFalse(getattr(caught.exception, "retryable", True))

    def test_stream_accepts_openai_usage_contract(self):
        adapter = PolzaChatAdapter(
            api_key="pza_test",
            base_url="https://polza.ai/api/v1",
        )
        body = (
            'data: {"id":"req-1","choices":[{"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
            'data: {"id":"req-1","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":1}}\n\n'
            'data: [DONE]\n\n'
        ).encode()

        @contextmanager
        def fake_stream(*args, **kwargs):
            response = httpx.Response(
                200,
                content=body,
                headers={"content-type": "text/event-stream"},
                request=httpx.Request(
                    "POST",
                    "https://polza.ai/api/v1/chat/completions",
                ),
            )
            yield response

        with patch("apps.ai_registry.adapters.httpx.stream", fake_stream):
            events = list(
                adapter.stream(
                    model="openai/gpt-6-luna",
                    messages=[{"role": "user", "content": "test"}],
                    max_output_tokens=16,
                )
            )

        self.assertEqual(events[0].kind, "delta")
        self.assertEqual(events[0].text_delta, "OK")
        self.assertEqual(events[-1].kind, "completed")
        self.assertEqual(events[-1].input_tokens, 5)
        self.assertEqual(events[-1].output_tokens, 1)
