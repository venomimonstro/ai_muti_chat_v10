from django.test import TestCase

from .models import Provider, ProviderApiKey


class ProviderKeySelectionTests(TestCase):
    def _key(self, provider, label, secret, health, priority):
        item = ProviderApiKey(
            provider=provider,
            label=label,
            health_state=health,
            priority=priority,
        )
        item.set_secret(secret)
        item.save()
        return item

    def test_healthy_key_wins_over_higher_priority_degraded_key(self):
        provider = Provider.objects.create(slug="key-selection", name="Key selection")
        self._key(
            provider,
            "degraded",
            "degraded-secret",
            ProviderApiKey.HealthState.DEGRADED,
            1,
        )
        self._key(
            provider,
            "healthy",
            "healthy-secret",
            ProviderApiKey.HealthState.HEALTHY,
            100,
        )

        self.assertEqual(provider.get_api_key(), "healthy-secret")

    def test_priority_is_respected_inside_same_health_tier(self):
        provider = Provider.objects.create(slug="key-priority", name="Key priority")
        self._key(
            provider,
            "secondary",
            "secondary-secret",
            ProviderApiKey.HealthState.HEALTHY,
            100,
        )
        self._key(
            provider,
            "primary",
            "primary-secret",
            ProviderApiKey.HealthState.HEALTHY,
            10,
        )

        self.assertEqual(provider.get_api_key(), "primary-secret")
