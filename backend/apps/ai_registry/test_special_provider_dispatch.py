from unittest.mock import patch

from django.test import TestCase

from . import adapters, dispatch
from .adapters import EchoProviderAdapter
from .models import AIModel, Provider


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
