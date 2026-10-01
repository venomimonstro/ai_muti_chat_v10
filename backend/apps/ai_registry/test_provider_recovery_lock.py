from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import reliability
from .models import Provider


class ProviderRecoveryLockTests(SimpleTestCase):
    @patch("apps.ai_registry.provider_recovery.cache.add", return_value=False)
    def test_follower_worker_does_not_probe_same_provider(self, cache_add):
        provider = SimpleNamespace(
            pk="provider-1",
            health_state=Provider.HealthState.DEGRADED,
            last_latency_ms=37,
        )

        with patch("apps.ai_registry.dispatch.adapter_for") as adapter_for:
            health = reliability.check_provider(provider)

        cache_add.assert_called_once()
        adapter_for.assert_not_called()
        self.assertFalse(health.healthy)
        self.assertEqual(health.error_code, "probe_in_progress")
        self.assertEqual(health.latency_ms, 37)
