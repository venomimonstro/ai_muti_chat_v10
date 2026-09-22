from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.ai_registry.models import Provider

from .signals import _commercial_fail_closed, _ensure, _require_procurement


class ProcurementRuntimeGateTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            slug="procurement-openai-test",
            name="Procurement OpenAI Test",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )

    @override_settings(PAYMENTS_LIVE_ENABLED=True, PROCUREMENT_RUNTIME_FAIL_CLOSED=False)
    def test_live_customer_payments_do_not_require_manual_provider_funding_account(self):
        self.assertFalse(_commercial_fail_closed())
        self.assertFalse(_require_procurement(self.provider))

    @override_settings(PAYMENTS_LIVE_ENABLED=True, PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    def test_explicit_strict_procurement_still_blocks_missing_funding_account(self):
        self.assertTrue(_commercial_fail_closed())
        with self.assertRaises(ValidationError):
            _require_procurement(self.provider)

    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=False)
    @patch("apps.procurement.signals._require_procurement", return_value=True)
    @patch("apps.procurement.signals.reserve_provider_spend")
    def test_optional_procurement_gap_does_not_block_client_request(self, reserve, _required):
        reserve.side_effect = ValidationError("Закупленный баланс AI-провайдера исчерпан")
        result = _ensure(
            provider=self.provider,
            expected_rub="10.00",
            snapshot={"fx_rate": "100"},
            source_key="chat:test:optional-ledger",
        )
        self.assertIsNone(result)

    @override_settings(PROCUREMENT_RUNTIME_FAIL_CLOSED=True)
    @patch("apps.procurement.signals._require_procurement", return_value=True)
    @patch("apps.procurement.signals.reserve_provider_spend")
    def test_strict_procurement_gap_still_blocks_client_request(self, reserve, _required):
        reserve.side_effect = ValidationError("Закупленный баланс AI-провайдера исчерпан")
        with self.assertRaises(ValidationError):
            _ensure(
                provider=self.provider,
                expected_rub="10.00",
                snapshot={"fx_rate": "100"},
                source_key="chat:test:strict-ledger",
            )
