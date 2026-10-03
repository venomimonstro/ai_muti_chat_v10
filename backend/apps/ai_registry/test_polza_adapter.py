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
