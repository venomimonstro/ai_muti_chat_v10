from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.ai_registry.adapters import DeepSeekChatAdapter, ProviderError, ProviderResult
from apps.ai_registry.client_readiness import provider_model_config_ready
from apps.ai_registry.models import Provider
from apps.ai_registry.reliability import _is_test_echo_provider
from apps.ai_registry.yandexgpt_adapter import YandexGPTAdapter, normalize_model_id


class YandexGPTRuntimeTests(SimpleTestCase):
    def test_model_uri_is_scoped_to_folder(self):
        self.assertEqual(
            normalize_model_id("yandexgpt/latest", folder_id="folder-123"),
            "gpt://folder-123/yandexgpt/latest",
        )
        self.assertEqual(
            normalize_model_id("gpt://folder-123/yandexgpt/latest", folder_id="other"),
            "gpt://folder-123/yandexgpt/latest",
        )

    def test_missing_folder_is_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ProviderError) as ctx:
                normalize_model_id("yandexgpt/latest")
        self.assertEqual(ctx.exception.code, "yandex_folder_missing")

    def test_client_readiness_requires_folder_or_full_uri(self):
        provider = SimpleNamespace(slug="yandexgpt", auth_config={})
        model = SimpleNamespace(provider=provider, upstream_model="yandexgpt/latest")
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(provider_model_config_ready(model))
        provider.auth_config = {"folder_id": "folder-123"}
        self.assertTrue(provider_model_config_ready(model))
        provider.auth_config = {}
        model.upstream_model = "gpt://folder-123/yandexgpt/latest"
        self.assertTrue(provider_model_config_ready(model))

    def test_generate_uses_openai_compatible_folder_uri(self):
        result = ProviderResult(
            text="OK",
            input_tokens=1,
            output_tokens=1,
            provider_request_id="test-yandex",
        )
        with patch.object(DeepSeekChatAdapter, "generate", return_value=result) as generate:
            adapter = YandexGPTAdapter(api_key="secret", folder_id="folder-123")
            returned = adapter.generate(
                model="yandexgpt/latest",
                messages=[{"role": "user", "content": "test"}],
                max_output_tokens=8,
            )
        self.assertEqual(returned.text, "OK")
        self.assertEqual(generate.call_args.kwargs["model"], "gpt://folder-123/yandexgpt/latest")

    def test_service_account_api_key_uses_yandex_authorization_scheme(self):
        adapter = YandexGPTAdapter(api_key="secret", folder_id="folder-123")
        self.assertEqual(adapter.headers["Authorization"], "Api-Key secret")
        self.assertNotIn("Bearer", adapter.headers["Authorization"])

    def test_yandexgpt_is_never_treated_as_internal_echo_provider(self):
        provider = SimpleNamespace(
            slug="yandexgpt",
            adapter_type=Provider.AdapterType.ECHO,
        )
        self.assertFalse(_is_test_echo_provider(provider))
