import os
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from . import adapters, dispatch
from .models import AIModel, Provider, ProviderApiKey


class HubAIProviderTests(TestCase):
    def test_seeded_provider_and_models_are_isolated_and_fail_closed(self):
        provider = Provider.objects.get(slug="hubai")
        self.assertEqual(provider.name, "HubAI")
        self.assertEqual(provider.adapter_type, Provider.AdapterType.DEEPSEEK_CHAT)
        self.assertEqual(provider.api_base_url, "https://hubai.loe.gg/v1")
        self.assertEqual(provider.credential_env, "HUBAI_API_KEY")
        self.assertFalse(provider.enabled)

        models = {
            model.upstream_model: model
            for model in AIModel.objects.filter(provider=provider).select_related("current_version")
        }
        self.assertEqual(
            set(models),
            {
                "deepseek-chat-fast",
                "deepseek-reasoner-fast",
                "deepseek-chat",
                "deepseek-reasoner",
            },
        )
        for upstream, model in models.items():
            self.assertFalse(model.enabled)
            self.assertIsNotNone(model.current_version)
            self.assertEqual(model.current_version.exact_api_id, upstream)

    def test_dispatch_uses_hubai_endpoint_not_official_deepseek_fallback(self):
        provider = Provider.objects.get(slug="hubai")
        model = AIModel.objects.get(provider=provider, upstream_model="deepseek-chat-fast")

        # Exercise the provider-specific fallback rather than the persisted URL.
        provider.api_base_url = ""
        with patch.dict(
            os.environ,
            {
                "HUBAI_API_KEY": "hubai-test-key",
                "HUBAI_API_BASE_URL": "https://hubai.loe.gg/v1",
                "DEEPSEEK_API_BASE_URL": "https://api.deepseek.com",
            },
            clear=False,
        ):
            adapter = dispatch.adapter_for(model, require_funding_balance=False)

        self.assertIsInstance(adapter, adapters.DeepSeekChatAdapter)
        self.assertEqual(adapter.base_url, "https://hubai.loe.gg/v1")
        self.assertNotEqual(adapter.base_url, "https://api.deepseek.com")

    def test_bootstrap_stores_key_encrypted_and_marks_transport_healthy(self):
        with (
            patch.dict(
                os.environ,
                {
                    "HUBAI_API_KEY": "hubai-bootstrap-secret",
                    "HUBAI_API_BASE_URL": "https://hubai.loe.gg/v1",
                },
                clear=False,
            ),
            patch.object(
                adapters.DeepSeekChatAdapter,
                "health_check",
                return_value=adapters.AdapterHealth(healthy=True, latency_ms=42),
            ),
        ):
            call_command("bootstrap_hubai")

        provider = Provider.objects.get(slug="hubai")
        key = ProviderApiKey.objects.get(provider=provider, label="HubAI primary")
        self.assertEqual(key.get_secret(), "hubai-bootstrap-secret")
        self.assertNotIn("hubai-bootstrap-secret", key.secret_encrypted)
        self.assertEqual(key.health_state, ProviderApiKey.HealthState.HEALTHY)
        self.assertEqual(provider.health_state, Provider.HealthState.HEALTHY)
        self.assertFalse(provider.enabled)
        self.assertFalse(
            AIModel.objects.filter(provider=provider, enabled=True).exists()
        )
