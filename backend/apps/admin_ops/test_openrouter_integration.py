from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from apps.ai_registry.adapters import OpenRouterChatAdapter, adapter_for
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import FxRateSnapshot, PriceVersion

from .openrouter_pricing import sync_openrouter_prices
from .provider_views import _check_key


class OpenRouterIntegrationTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.get(slug="openrouter")
        self.provider.name = "OpenRouter"
        self.provider.enabled = True
        self.provider.emergency_disabled = False
        self.provider.adapter_type = Provider.AdapterType.XAI_CHAT
        self.provider.api_base_url = "https://openrouter.ai/api/v1"
        self.provider.credential_env = "OPENROUTER_API_KEY"
        self.provider.health_state = Provider.HealthState.HEALTHY
        self.provider.consecutive_failures = 0
        self.provider.circuit_opened_until = None
        self.provider.save(
            update_fields=[
                "name",
                "enabled",
                "emergency_disabled",
                "adapter_type",
                "api_base_url",
                "credential_env",
                "health_state",
                "consecutive_failures",
                "circuit_opened_until",
            ]
        )
        self.provider.api_keys.all().delete()
        self.provider.models.all().delete()

        self.key = ProviderApiKey(
            provider=self.provider,
            label="Ключ 1",
            enabled=True,
            priority=10,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        self.key.set_secret("sk-or-v1-test-key")
        self.key.save()
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="openrouter-openai-gpt-4o-mini",
            display_name="OpenAI GPT-4o mini via OpenRouter",
            upstream_model="openai/gpt-4o-mini",
            enabled=False,
            capabilities=["text", "streaming"],
            routing_tags=["admin-selected"],
        )
        FxRateSnapshot.objects.create(
            base_currency="USD",
            quote_currency="RUB",
            rate=Decimal("90"),
            source="test",
            effective_at=timezone.now(),
        )

    def test_openrouter_uses_dedicated_openai_compatible_chat_adapter(self):
        adapter = adapter_for(self.model)
        self.assertIsInstance(adapter, OpenRouterChatAdapter)
        self.assertEqual(adapter.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(adapter.api_key, "sk-or-v1-test-key")

    @patch("apps.admin_ops.provider_views.httpx.get")
    def test_openrouter_key_is_validated_with_current_key_endpoint(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "label": "sk-or-v1-test...key",
                "limit": 100,
                "limit_remaining": 75,
                "usage": 25,
            }
        }
        get.return_value = response

        healthy = _check_key(self.provider, self.key)

        self.assertTrue(healthy)
        self.key.refresh_from_db()
        self.assertEqual(self.key.health_state, ProviderApiKey.HealthState.HEALTHY)
        self.assertEqual(self.key.last_error_code, "")
        self.assertEqual(get.call_args.args[0], "https://openrouter.ai/api/v1/key")
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Bearer sk-or-v1-test-key",
        )

    @patch("apps.admin_ops.openrouter_pricing.httpx.get")
    def test_live_catalog_pricing_creates_usd_price_version(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [
                {
                    "id": "openai/gpt-4o-mini",
                    "context_length": 128000,
                    "pricing": {
                        "prompt": "0.00000015",
                        "completion": "0.00000060",
                    },
                }
            ]
        }
        get.return_value = response

        result = sync_openrouter_prices(self.provider, ["openai/gpt-4o-mini"])

        self.assertEqual(len(result["verified"]), 1)
        self.assertEqual(result["rejected"], [])
        price = PriceVersion.objects.get(model_slug=self.model.slug, active=True)
        self.assertEqual(price.provider_currency, "USD")
        self.assertEqual(price.input_price_per_million, Decimal("0.150000"))
        self.assertEqual(price.output_price_per_million, Decimal("0.600000"))
        self.assertEqual(price.input_rub_per_million, Decimal("13.5000"))
        self.assertEqual(price.output_rub_per_million, Decimal("54.0000"))
        self.model.refresh_from_db()
        self.assertEqual(self.model.context_window, 128000)
        self.assertEqual(get.call_args.args[0], "https://openrouter.ai/api/v1/models")
        self.assertEqual(
            get.call_args.kwargs["headers"]["Authorization"],
            "Bearer sk-or-v1-test-key",
        )
