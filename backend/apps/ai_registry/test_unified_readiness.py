from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.procurement.models import ProviderFundingAccount

from .models import Provider, ProviderApiKey
from .reliability import provider_available


class UnifiedReadinessTests(TestCase):
    def _provider_with_key(self, *, health=Provider.HealthState.HEALTHY):
        provider = Provider.objects.create(
            slug="readiness-provider",
            name="Readiness Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=health,
        )
        key = ProviderApiKey(provider=provider, label="primary", enabled=True)
        key.set_secret("sk-test-readiness")
        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.save()
        return provider, key

    def test_expired_transient_open_circuit_still_blocks_customer_traffic(self):
        provider, _key = self._provider_with_key(health=Provider.HealthState.OPEN)
        provider.circuit_opened_until = timezone.now() - timedelta(seconds=1)
        provider.save(update_fields=["circuit_opened_until"])
        # Only the background health watcher may probe and re-admit this channel.
        self.assertFalse(provider_available(provider))

    def test_unknown_provider_never_uses_customer_as_health_probe(self):
        provider, _key = self._provider_with_key(health=Provider.HealthState.UNKNOWN)
        self.assertFalse(provider_available(provider))

    def test_persistent_open_circuit_remains_blocked(self):
        provider, _key = self._provider_with_key(health=Provider.HealthState.OPEN)
        provider.circuit_opened_until = None
        provider.save(update_fields=["circuit_opened_until"])
        self.assertFalse(provider_available(provider))

    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_zero_procurement_balance_blocks_customer_traffic(self):
        provider, key = self._provider_with_key()
        ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=key,
            label="Primary funding",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=0,
        )
        self.assertFalse(provider_available(provider))

    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_positive_procurement_balance_allows_customer_traffic(self):
        provider, key = self._provider_with_key()
        ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=key,
            label="Primary funding",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=10,
        )
        with patch.object(key, "get_secret", return_value="sk-test-readiness"):
            self.assertTrue(provider_available(provider))
