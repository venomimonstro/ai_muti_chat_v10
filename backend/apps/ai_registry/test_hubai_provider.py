import os
from unittest.mock import patch

import httpx
from django.test import TestCase

from apps.admin_ops.provider_views import HUBAI_MODELS, _check_hubai_key, _hubai_catalog

from . import adapters, dispatch
from .models import AIModel, Provider, ProviderApiKey


class HubAIProviderTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get(slug="hubai")
        self.provider.api_keys.all().delete()
        self.model = AIModel.objects.get(
            provider=self.provider,
            upstream_model="deepseek-chat-fast",
        )

    def _key(self, secret="hubai-test-secret"):
        item = ProviderApiKey(
            provider=self.provider,
            label="HubAI test",
            priority=10,
            health_state=ProviderApiKey.HealthState.UNKNOWN,
        )
        item.set_secret(secret)
        item.save()
        return item

    def test_seeded_provider_is_admin_only_and_fail_closed(self):
        self.assertEqual(self.provider.name, "HubAI")
        self.assertEqual(
            self.provider.adapter_type,
            Provider.AdapterType.DEEPSEEK_CHAT,
        )
        self.assertEqual(
            self.provider.api_base_url,
            "https://hubai.loe.gg/v1",
        )
        self.assertEqual(self.provider.credential_env, "")
        self.assertFalse(self.provider.enabled)

        models = {
            model.upstream_model: model
            for model in AIModel.objects.filter(provider=self.provider)
            .select_related("current_version")
        }
        self.assertEqual(
            set(models),
            {model_id for model_id, _display_name in HUBAI_MODELS},
        )
        for upstream, model in models.items():
            self.assertFalse(model.enabled)
            self.assertIsNotNone(model.current_version)
            self.assertEqual(model.current_version.exact_api_id, upstream)

    def test_environment_secret_is_not_a_hubai_credential_source(self):
        with patch.dict(
            os.environ,
            {"HUBAI_API_KEY": "must-not-be-used"},
            clear=False,
        ):
            self.assertFalse(self.provider.credential_configured())
            self.assertEqual(self.provider.credential_source(), "none")

    def test_admin_key_is_encrypted_at_rest(self):
        item = self._key("super-secret-hubai-key")

        self.assertNotIn(
            "super-secret-hubai-key",
            item.secret_encrypted,
        )
        self.assertEqual(
            item.get_secret(),
            "super-secret-hubai-key",
        )
        self.assertEqual(
            self.provider.credential_source(),
            "key_pool",
        )

    @patch("apps.admin_ops.provider_views.httpx.post")
    @patch("apps.admin_ops.provider_views.httpx.get")
    def test_admin_key_check_falls_back_to_chat_when_models_is_missing(
        self,
        get,
        post,
    ):
        get.return_value = httpx.Response(
            404,
            request=httpx.Request(
                "GET",
                "https://hubai.loe.gg/v1/models",
            ),
        )
        post.return_value = httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://hubai.loe.gg/v1/chat/completions",
            ),
            json={
                "id": "probe",
                "choices": [{"message": {"content": "ok"}}],
            },
        )
        item = self._key()

        self.assertTrue(_check_hubai_key(self.provider, item))
        item.refresh_from_db()
        self.assertEqual(
            item.health_state,
            ProviderApiKey.HealthState.HEALTHY,
        )
        self.assertEqual(item.last_error_code, "")
        post.assert_called_once()

    @patch("apps.admin_ops.provider_views.httpx.get")
    def test_invalid_admin_key_is_marked_degraded(self, get):
        get.return_value = httpx.Response(
            401,
            request=httpx.Request(
                "GET",
                "https://hubai.loe.gg/v1/models",
            ),
            json={"error": {"code": "invalid_api_key"}},
        )
        item = self._key()

        self.assertFalse(_check_hubai_key(self.provider, item))
        item.refresh_from_db()
        self.assertEqual(
            item.health_state,
            ProviderApiKey.HealthState.DEGRADED,
        )
        self.assertEqual(
            item.last_error_code,
            "invalid_api_key",
        )

    def test_admin_catalog_is_fixed_to_supported_models(self):
        catalog = _hubai_catalog(self.provider)

        self.assertEqual(
            [item["id"] for item in catalog],
            [model_id for model_id, _display_name in HUBAI_MODELS],
        )
        self.assertTrue(all(item["selected"] for item in catalog))

    def test_dispatch_uses_dedicated_hubai_adapter_and_admin_key(self):
        item = self._key()
        item.health_state = ProviderApiKey.HealthState.HEALTHY
        item.save(update_fields=["health_state"])

        adapter = dispatch.adapter_for(
            self.model,
            allow_probe=True,
            require_funding_balance=False,
        )

        self.assertIsInstance(adapter, adapters.HubAIChatAdapter)
        self.assertEqual(
            adapter.base_url,
            "https://hubai.loe.gg/v1",
        )
        self.assertEqual(
            adapter.api_key,
            "hubai-test-secret",
        )

    @patch("apps.ai_registry.adapters.httpx.post")
    @patch("apps.ai_registry.adapters.httpx.get")
    def test_runtime_health_matches_admin_fallback_behavior(
        self,
        get,
        post,
    ):
        get.return_value = httpx.Response(
            404,
            request=httpx.Request(
                "GET",
                "https://hubai.loe.gg/v1/models",
            ),
        )
        post.return_value = httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://hubai.loe.gg/v1/chat/completions",
            ),
            json={
                "id": "runtime-probe",
                "choices": [{"message": {"content": "ok"}}],
            },
        )
        adapter = adapters.HubAIChatAdapter(
            api_key="hubai-test-secret",
            base_url="https://hubai.loe.gg/v1",
        )

        health = adapter.health_check()

        self.assertTrue(health.healthy)
        post.assert_called_once()
