from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey


@override_settings(ADMIN_MFA_ENFORCED=False)
class ProviderKeyInferenceGateTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-key-inference-gate",
            email="admin-key-inference-gate@example.test",
            password="test-password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.provider = Provider.objects.create(
            slug="key-inference-gate",
            name="Key Inference Gate",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            api_base_url="https://example.test/v1",
            enabled=True,
            health_state=Provider.HealthState.OPEN,
            consecutive_failures=5,
            circuit_opened_until=None,
        )
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="key-inference-gate-model",
            display_name="Key Inference Gate Model",
            upstream_model="model-v1",
            enabled=True,
            capabilities=["text", "streaming"],
        )

    def _healthy_key(self, label, secret):
        key = ProviderApiKey(
            provider=self.provider,
            label=label,
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        key.set_secret(secret)
        key.save()
        return key

    @staticmethod
    def _mark_key_healthy(_provider, key):
        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.last_error_code = ""
        key.save(update_fields=["health_state", "last_error_code"])
        return True

    @patch("apps.admin_ops.provider_key_views._refresh_balance")
    @patch("apps.admin_ops.provider_key_views._check_key")
    def test_healthy_credential_does_not_re_admit_open_provider(
        self, check_key, refresh_balance
    ):
        check_key.side_effect = self._mark_key_healthy
        refresh_balance.return_value = None

        response = self.client.post(
            f"/api/v1/admin/providers/{self.provider.slug}/keys/",
            {"api_key": "test-secret", "label": "Recovery key"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["provider_verification_pending"])

        self.provider.refresh_from_db()
        self.assertEqual(self.provider.health_state, Provider.HealthState.UNKNOWN)
        self.assertEqual(self.provider.consecutive_failures, 5)
        self.assertTrue(
            self.provider.api_keys.filter(
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ).exists()
        )

        models = self.client.get("/api/v1/models/")
        self.assertEqual(models.status_code, 200, models.data)
        self.assertNotIn(self.model.slug, [item["slug"] for item in models.data])

    def test_deleting_key_with_healthy_spare_does_not_re_admit_open_provider(self):
        doomed = self._healthy_key("Primary", "primary-secret")
        spare = self._healthy_key("Spare", "spare-secret")

        response = self.client.delete(
            f"/api/v1/admin/providers/{self.provider.slug}/keys/{doomed.id}/"
        )
        self.assertEqual(response.status_code, 204)

        self.provider.refresh_from_db()
        spare.refresh_from_db()
        self.assertFalse(ProviderApiKey.objects.filter(pk=doomed.pk).exists())
        self.assertTrue(spare.enabled)
        self.assertEqual(spare.health_state, ProviderApiKey.HealthState.HEALTHY)
        self.assertEqual(self.provider.health_state, Provider.HealthState.OPEN)
        self.assertEqual(self.provider.consecutive_failures, 5)

        models = self.client.get("/api/v1/models/")
        self.assertEqual(models.status_code, 200, models.data)
        self.assertNotIn(self.model.slug, [item["slug"] for item in models.data])
