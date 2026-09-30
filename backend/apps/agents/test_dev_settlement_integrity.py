from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.ai_registry.adapters import ProviderError

from .dev_model_execution import execute_with_model_fallback
from .dev_model_fallback import DevModelAttempt


class DevSettlementIntegrityTests(SimpleTestCase):
    def test_provider_delivery_never_releases_reservations_on_settlement_failure(self):
        provider = SimpleNamespace(slug="provider-a")
        model = SimpleNamespace(
            pk=1,
            slug="model-a",
            upstream_model="model-a",
            max_output_tokens=1000,
            provider=provider,
        )
        price = SimpleNamespace(id="price-version-1")
        preflight = SimpleNamespace(
            user_charge_rub=Decimal("10"),
            provider_cost_rub=Decimal("1"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        )
        attempt = DevModelAttempt(
            model=model,
            output_tokens=1000,
            preflight=preflight,
            estimated_charge_rub=Decimal("10"),
            rank=1,
            price=price,
        )
        customer = SimpleNamespace(id="customer-reservation", amount_rub=Decimal("10"))
        provider_reservation = SimpleNamespace(id="provider-reservation")
        delivered = SimpleNamespace(
            text="done",
            input_tokens=123,
            output_tokens=45,
            provider_request_id="provider-request-1",
        )
        actual_quote = SimpleNamespace(
            user_charge_rub=Decimal("8"),
            provider_cost_rub=Decimal("1"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        )

        with (
            patch("apps.agents.dev_model_execution.plan_model_attempts", return_value=[attempt]),
            patch("apps.agents.dev_model_execution.reserve", return_value=customer),
            patch("apps.agents.dev_model_execution.reserve_agent_provider_spend", return_value=provider_reservation),
            patch("apps.agents.dev_model_execution.generate_with_key_failover", return_value=(delivered, 1)),
            patch("apps.agents.dev_model_execution.quote", return_value=actual_quote),
            patch("apps.agents.dev_model_execution.require_margin", side_effect=lambda value: value),
            patch(
                "apps.agents.dev_model_execution.settle_agent_provider_spend",
                side_effect=ValidationError("database unavailable"),
            ),
            patch("apps.agents.dev_model_execution.settle"),
            patch("apps.agents.dev_model_execution.release") as release_customer,
            patch("apps.agents.dev_model_execution.release_agent_provider_spend") as release_provider,
        ):
            with self.assertRaises(ProviderError) as raised:
                execute_with_model_fallback(
                    run=SimpleNamespace(id="run-id", owner=SimpleNamespace()),
                    sequence=2,
                    primary_model=model,
                    messages=[],
                    estimated_input_tokens=100,
                    requested_output_tokens=1000,
                    remaining_budget_rub=Decimal("20"),
                    is_canceled=lambda: False,
                )

        self.assertEqual(raised.exception.code, "dev_settlement_interrupted")
        self.assertEqual(raised.exception.model_attempts[-1]["status"], "settlement_interrupted")
        self.assertEqual(raised.exception.model_attempts[-1]["provider_request_id"], "provider-request-1")
        self.assertEqual(raised.exception.model_attempts[-1]["input_tokens"], 123)
        self.assertEqual(raised.exception.model_attempts[-1]["output_tokens"], 45)
        release_customer.assert_not_called()
        release_provider.assert_not_called()
