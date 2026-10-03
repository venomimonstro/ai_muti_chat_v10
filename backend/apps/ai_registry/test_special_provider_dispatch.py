from unittest.mock import patch

from django.test import TestCase

from . import adapters, dispatch
from .adapters import EchoProviderAdapter
from .models import AIModel, Provider, ProviderApiKey


class SpecialProviderDispatchTests(TestCase):
    def test_runtime_dispatcher_is_installed_by_app_startup(self):
        self.assertIs(adapters.adapter_for, dispatch.adapter_for)

    def test_gigachat_never_falls_through_to_echo(self):
        provider, _created = Provider.objects.update_or_create(
            slug="gigachat",
            defaults={
                "name": "GigaChat",
                "adapter_type": Provider.AdapterType.ECHO,
                "credential_secret": "",
                "enabled": True,
                "emergency_disabled": False,
            },
        )
        model = AIModel.objects.create(
            provider=provider,
            slug="dispatch-gigachat",
            display_name="GigaChat",
            upstream_model="GigaChat-2-Pro",
        )
        with patch.object(provider, "get_api_key", return_value="Basic test-secret"):
            adapter = dispatch.adapter_for(model)
        self.assertNotIsInstance(adapter, EchoProviderAdapter)
        self.assertEqual(type(adapter).__name__, "GigaChatAPIAdapter")

    def test_polza_uses_dedicated_gateway_adapter(self):
        provider, _created = Provider.objects.update_or_create(
            slug="polza",
            defaults={
                "name": "Polza.ai",
                "adapter_type": Provider.AdapterType.XAI_CHAT,
                "api_base_url": "https://polza.ai/api/v1",
                "enabled": True,
                "emergency_disabled": False,
            },
        )
        key = ProviderApiKey(
            provider=provider,
            label="Polza test",
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        key.set_secret("pza_test_secret")
        key.save()
        model = AIModel.objects.create(
            provider=provider,
            slug="dispatch-polza",
            display_name="Polza GPT",
            upstream_model="openai/gpt-6-luna",
        )
        adapter = dispatch.adapter_for(
            model,
            require_funding_balance=False,
        )
        self.assertEqual(type(adapter).__name__, "PolzaChatAdapter")
        self.assertEqual(adapter.base_url, "https://polza.ai/api/v1")
        self.assertEqual(adapter.api_key, "pza_test_secret")

    def test_real_echo_provider_still_uses_echo(self):
        provider = Provider.objects.create(
            slug="echo-test",
            name="Echo",
            adapter_type=Provider.AdapterType.ECHO,
        )
        model = AIModel.objects.create(
            provider=provider,
            slug="dispatch-echo",
            display_name="Echo",
            upstream_model="echo-v1",
        )
        with patch.object(provider, "get_api_key", return_value=""):
            adapter = dispatch.adapter_for(model)
        self.assertIsInstance(adapter, EchoProviderAdapter)
