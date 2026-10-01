from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import create_funding_account


@override_settings(ADMIN_MFA_ENFORCED=False)
class ProcurementRecoveryTriggerTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-procurement-recovery",
            email="admin-procurement-recovery@example.test",
            password="test-password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.provider = Provider.objects.create(
            slug="procurement-recovery-provider",
            name="Procurement Recovery Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            api_base_url="https://example.test/v1",
            enabled=True,
            health_state=Provider.HealthState.OPEN,
            consecutive_failures=4,
            circuit_opened_until=None,
        )
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="procurement-recovery-model",
            display_name="Procurement Recovery Model",
            upstream_model="model-v1",
            enabled=True,
            capabilities=["text", "streaming"],
        )

    def _key(self, label):
        key = ProviderApiKey(
            provider=self.provider,
            label=label,
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        key.set_secret(f"secret-{label}")
        key.save()
        return key

    def test_funding_default_account_wakes_open_provider_without_marking_it_healthy(self):
        key = self._key("primary")

        response = self.client.post(
            "/api/v1/admin/procurement/ledger/",
            {
                "action": "purchase_key",
                "api_key_id": str(key.id),
                "credit_currency": "USD",
                "credit_native": "10.000000",
                "base_cost_rub": "1000.00",
                "fees_rub": "0",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

        account = ProviderFundingAccount.objects.get(api_key=key)
        self.assertTrue(account.is_default)
        self.assertGreater(account.available_native, Decimal("0"))

        self.provider.refresh_from_db()
        self.assertEqual(self.provider.health_state, Provider.HealthState.UNKNOWN)
        self.assertEqual(self.provider.consecutive_failures, 4)

    def test_switching_default_credential_requires_fresh_inference_proof(self):
        first = self._key("first")
        second = self._key("second")
        first_account = create_funding_account(
            provider=self.provider,
            api_key=first,
            label="First",
            currency="USD",
            is_default=True,
        )
        second_account = create_funding_account(
            provider=self.provider,
            api_key=second,
            label="Second",
            currency="USD",
            is_default=False,
        )
        ProviderFundingAccount.objects.filter(pk=first_account.pk).update(
            funded_native=Decimal("10.000000")
        )
        ProviderFundingAccount.objects.filter(pk=second_account.pk).update(
            funded_native=Decimal("10.000000")
        )
        self.provider.health_state = Provider.HealthState.HEALTHY
        self.provider.consecutive_failures = 0
        self.provider.save(update_fields=["health_state", "consecutive_failures"])

        response = self.client.post(
            "/api/v1/admin/procurement/ledger/",
            {"action": "set_default", "account_id": str(second_account.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        first_account.refresh_from_db()
        second_account.refresh_from_db()
        self.assertFalse(first_account.is_default)
        self.assertTrue(second_account.is_default)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.health_state, Provider.HealthState.UNKNOWN)
