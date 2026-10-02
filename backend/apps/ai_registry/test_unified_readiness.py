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
    def test_no_procurement_ledger_does_not_block_healthy_customer_key(self):
        provider, _key = self._provider_with_key()
        self.assertTrue(provider_available(provider))

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


    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_degraded_default_with_healthy_funded_backup_keeps_provider_available(self):
        provider = Provider.objects.create(
            slug="readiness-backup-provider",
            name="Readiness Backup Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=Provider.HealthState.DEGRADED,
        )
        degraded = ProviderApiKey(
            provider=provider,
            label="degraded-default",
            enabled=True,
            health_state=ProviderApiKey.HealthState.DEGRADED,
        )
        degraded.set_secret("sk-degraded")
        degraded.save()
        healthy = ProviderApiKey(
            provider=provider,
            label="healthy-backup",
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        healthy.set_secret("sk-healthy-backup")
        healthy.save()
        ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=degraded,
            label="Degraded default",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=100,
            priority=1,
        )
        backup = ProviderFundingAccount.objects.create(
            provider=provider,
            api_key=healthy,
            label="Healthy backup",
            currency="USD",
            active=True,
            is_default=False,
            funded_native=100,
            priority=10,
        )

        self.assertTrue(provider_available(provider))

        from .dispatch import select_runtime_api_key

        secret, key_id = select_runtime_api_key(
            provider,
            allow_probe=False,
            touch=False,
        )
        self.assertEqual(secret, "sk-healthy-backup")
        self.assertEqual(key_id, healthy.id)
        self.assertEqual(
            backup.api_key_id,
            key_id,
        )
