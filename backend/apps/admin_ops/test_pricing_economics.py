from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, ModelVersion, Provider
from apps.billing.models import FxRateSnapshot, MarginPolicyVersion, MarkupRuleVersion, PriceVersion
from apps.billing.pricing import active_price, quote, require_margin, MarginFloorError


@override_settings(ADMIN_MFA_ENFORCED=False)
class PricingEconomicsTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="pricing-admin",
            email="pricing-admin@example.test",
            password="password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.client.force_authenticate(self.admin)
        self.provider = Provider.objects.create(
            slug="pricing-provider",
            name="Pricing Provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            api_base_url="https://example.test/v1",
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="pricing-model",
            display_name="Pricing Model",
            upstream_model="pricing-model-v1",
            enabled=True,
        )
        version = ModelVersion.objects.create(
            model=self.model,
            version="v1",
            exact_api_id="pricing-model-v1",
            stage=ModelVersion.Stage.ACTIVE,
            activated_at=timezone.now(),
        )
        self.model.current_version = version
        self.model.save(update_fields=["current_version"])
        FxRateSnapshot.objects.create(
            base_currency="USD",
            quote_currency="RUB",
            rate=Decimal("100"),
            source="test",
            source_reference="test",
            effective_at=timezone.now(),
        )
        PriceVersion.objects.create(
            model_slug=self.model.slug,
            input_rub_per_million=Decimal("100"),
            output_rub_per_million=Decimal("200"),
            provider_currency="USD",
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("2"),
            markup_percent=Decimal("50"),
            active=True,
            effective_from=timezone.now(),
        )
        MarginPolicyVersion.objects.create(
            minimum_gross_margin_percent=Decimal("25"),
            anomaly_cost_deviation_percent=Decimal("20"),
            reconciliation_threshold_rub=Decimal("1"),
            active=True,
            effective_from=timezone.now(),
        )

    def test_model_markup_controls_client_charge_and_profit(self):
        response = self.client.post(
            "/api/v1/admin/procurement/",
            {"action": "set_markup", "model": self.model.slug, "markup_percent": "50"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        value = quote(
            active_price(self.model.slug),
            1_000_000,
            0,
            provider_slug=self.provider.slug,
            model_slug=self.model.slug,
        )
        self.assertEqual(value.provider_cost_rub, Decimal("100.0000"))
        self.assertEqual(value.user_charge_rub, Decimal("150.0000"))
        self.assertEqual(value.gross_profit_rub, Decimal("50.0000"))
        self.assertEqual(value.gross_margin_percent, Decimal("33.333"))
        require_margin(value)

    def test_markup_below_margin_floor_is_rejected(self):
        response = self.client.post(
            "/api/v1/admin/procurement/",
            {"action": "set_markup", "model": self.model.slug, "markup_percent": "20"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            MarkupRuleVersion.objects.filter(
                scope_type=MarkupRuleVersion.Scope.MODEL,
                scope_key=self.model.slug,
                active=True,
            ).exists()
        )
