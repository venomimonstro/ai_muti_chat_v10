from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderApiKey
from apps.billing.models import MarginPolicyVersion, PriceVersion


@override_settings(ADMIN_MFA_ENFORCED=False)
class ProviderClientActivationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-client-activation",
            email="admin-client-activation@example.test",
            password="test-password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.provider = Provider.objects.create(
            slug="activation-provider",
            name="Activation Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            api_base_url="https://example.test/v1",
            enabled=False,
            health_state=Provider.HealthState.HEALTHY,
        )
        key = ProviderApiKey(
            provider=self.provider,
            label="Основной",
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        key.set_secret("secret-test-key")
        key.save()
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="activation-model",
            display_name="Activation Model",
            upstream_model="activation-upstream",
            enabled=False,
            capabilities=["text", "streaming"],
        )
        version = ModelVersion.objects.create(
            model=self.model,
            version="v1",
            exact_api_id="activation-upstream",
            capabilities=self.model.capabilities,
            routing_tags=[],
            context_window=self.model.context_window,
            max_output_tokens=self.model.max_output_tokens,
            stage=ModelVersion.Stage.ACTIVE,
            activated_at=timezone.now(),
        )
        self.model.current_version = version
        self.model.save(update_fields=["current_version"])
        PriceVersion.objects.create(
            model_slug=self.model.slug,
            input_rub_per_million=Decimal("150.0000"),
            output_rub_per_million=Decimal("300.0000"),
            provider_currency="RUB",
            input_price_per_million=Decimal("100.000000"),
            output_price_per_million=Decimal("200.000000"),
            markup_percent=Decimal("50.00"),
            active=True,
            effective_from=timezone.now(),
        )
        MarginPolicyVersion.objects.create(
            minimum_gross_margin_percent=Decimal("25.00"),
            anomaly_cost_deviation_percent=Decimal("20.00"),
            reconciliation_threshold_rub=Decimal("1.00"),
            active=True,
            effective_from=timezone.now(),
        )

    def test_activation_exposes_model_to_client_catalog(self):
        response = self.client.post(
            f"/api/v1/admin/providers/{self.provider.slug}/activate-models/",
            {"model_ids": ["activation-upstream"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["activated"]), 1)
        self.assertEqual(response.data["blocked"], [])

        self.model.refresh_from_db()
        self.provider.refresh_from_db()
        self.assertTrue(self.model.enabled)
        self.assertTrue(self.provider.enabled)

        models = self.client.get("/api/v1/models/")
        self.assertEqual(models.status_code, 200, models.data)
        slugs = [item["slug"] for item in models.data]
        self.assertIn(self.model.slug, slugs)

    def test_activation_reports_model_without_safe_price(self):
        PriceVersion.objects.filter(model_slug=self.model.slug).update(active=False)
        response = self.client.post(
            f"/api/v1/admin/providers/{self.provider.slug}/activate-models/",
            {"model_ids": ["activation-upstream"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["activated"], [])
        self.assertEqual(len(response.data["blocked"]), 1)
        self.model.refresh_from_db()
        self.assertFalse(self.model.enabled)
