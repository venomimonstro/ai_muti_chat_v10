import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.ai_registry.models import Provider

from .models import ProviderFundingAccount
from .readiness import quote_has_procurement_capacity


@override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
class RequestCapacityTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="capacity-provider",
            name="Capacity provider",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            credential_env="CAPACITY_PROVIDER_KEY",
            health_state=Provider.HealthState.HEALTHY,
        )
        self.account = ProviderFundingAccount.objects.create(
            provider=self.provider,
            label="Primary",
            credential_env="CAPACITY_PROVIDER_KEY",
            currency="USD",
            active=True,
            is_default=True,
            funded_native=Decimal("2.000000"),
        )

    def _quote(self, *, provider_cost_rub, fx_rate="100", currency="USD"):
        return SimpleNamespace(
            provider_cost_rub=Decimal(str(provider_cost_rub)),
            pricing_snapshot={
                "provider_currency": currency,
                "fx_rate": str(fx_rate),
            },
        )

    def test_exact_request_capacity_allows_route(self):
        with patch.dict(os.environ, {"CAPACITY_PROVIDER_KEY": "sk-test"}):
            self.assertTrue(
                quote_has_procurement_capacity(
                    self.provider,
                    self._quote(provider_cost_rub="150"),
                )
            )

    def test_positive_but_insufficient_balance_rejects_route(self):
        with patch.dict(os.environ, {"CAPACITY_PROVIDER_KEY": "sk-test"}):
            self.assertFalse(
                quote_has_procurement_capacity(
                    self.provider,
                    self._quote(provider_cost_rub="250"),
                )
            )

    def test_reserved_balance_is_not_double_spent(self):
        self.account.reserved_native = Decimal("1.500000")
        self.account.save(update_fields=["reserved_native", "updated_at"])
        with patch.dict(os.environ, {"CAPACITY_PROVIDER_KEY": "sk-test"}):
            self.assertFalse(
                quote_has_procurement_capacity(
                    self.provider,
                    self._quote(provider_cost_rub="60"),
                )
            )

    def test_currency_mismatch_fails_closed(self):
        with patch.dict(os.environ, {"CAPACITY_PROVIDER_KEY": "sk-test"}):
            self.assertFalse(
                quote_has_procurement_capacity(
                    self.provider,
                    self._quote(provider_cost_rub="10", currency="EUR"),
                )
            )
