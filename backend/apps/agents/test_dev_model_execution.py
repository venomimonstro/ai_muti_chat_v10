from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.ai_registry.adapters import ProviderError

from .dev_model_execution import DevStageCanceled, execute_with_model_fallback
from .dev_model_fallback import DevModelAttempt


def _model(pk, slug, provider):
    return SimpleNamespace(pk=pk, slug=slug, upstream_model=slug, max_output_tokens=1000, provider=SimpleNamespace(slug=provider))


def _attempt(model, rank, charge):
    preflight = SimpleNamespace(
        user_charge_rub=Decimal(charge),
        provider_cost_rub=Decimal("1"),
        fx_snapshot=SimpleNamespace(rate=Decimal("100")),
    )
    return DevModelAttempt(model=model, output_tokens=1000, preflight=preflight, estimated_charge_rub=Decimal(charge), rank=rank)


class DevModelExecutionTests(SimpleTestCase):
    def test_failed_primary_is_released_and_fallback_settles_once(self):
        primary = _model(1, "primary", "provider-a")
        fallback = _model(2, "fallback", "provider-b")
        attempts = [_attempt(primary, 1, "10"), _attempt(fallback, 2, "12")]
        reservations = [SimpleNamespace(id="customer-1", amount_rub=Decimal("10")), SimpleNamespace(id="customer-2", amount_rub=Decimal("12"))]
        provider_reservations = [SimpleNamespace(id="provider-1"), SimpleNamespace(id="provider-2")]
        success = SimpleNamespace(input_tokens=100, output_tokens=50, provider_request_id="req-2", text="ok")
        actual_quote = SimpleNamespace(
            user_charge_rub=Decimal("9"),
            provider_cost_rub=Decimal("1"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        )

        with (
            patch("apps.agents.dev_model_execution.plan_model_attempts", return_value=attempts),
            patch("apps.agents.dev_model_execution.reserve", side_effect=reservations) as reserve_customer,
            patch("apps.agents.dev_model_execution.reserve_agent_provider_spend", side_effect=provider_reservations) as reserve_provider,
            patch("apps.agents.dev_model_execution.generate_with_key_failover", side_effect=[ProviderError("down", code="provider_down", retryable=True), (success, 1)]),
            patch("apps.agents.dev_model_execution.release") as release_customer,
            patch("apps.agents.dev_model_execution.release_agent_provider_spend") as release_provider,
            patch("apps.agents.dev_model_execution.active_price", return_value=SimpleNamespace()),
            patch("apps.agents.dev_model_execution.quote", return_value=actual_quote),
            patch("apps.agents.dev_model_execution.require_margin", side_effect=lambda value: value),
            patch("apps.agents.dev_model_execution.settle") as settle_customer,
            patch("apps.agents.dev_model_execution.settle_agent_provider_spend") as settle_provider,
        ):
            result = execute_with_model_fallback(
                run=SimpleNamespace(id="run-id", owner=SimpleNamespace()),
                sequence=3,
                primary_model=primary,
                messages=[],
                estimated_input_tokens=100,
                requested_output_tokens=1000,
                remaining_budget_rub=Decimal("50"),
                is_canceled=lambda: False,
            )

        self.assertEqual(result.model.slug, "fallback")
        self.assertEqual(result.actual_rub, Decimal("9"))
        self.assertEqual([item["status"] for item in result.model_attempts], ["provider_failed", "completed"])
        self.assertEqual(reserve_customer.call_args_list[0].args[2], "agent-run:run-id:step:3:model:1")
        self.assertEqual(reserve_customer.call_args_list[1].args[2], "agent-run:run-id:step:3:model:2")
        self.assertEqual(reserve_provider.call_args_list[0].kwargs["source_key"], "agent:run-id:step:3:model:1")
        self.assertEqual(reserve_provider.call_args_list[1].kwargs["source_key"], "agent:run-id:step:3:model:2")
        release_customer.assert_called_once_with("customer-1")
        release_provider.assert_called_once_with(provider_reservations[0])
        settle_customer.assert_called_once_with("customer-2", Decimal("9"))
        settle_provider.assert_called_once()

    def test_cancel_after_reserve_releases_both_reservations(self):
        model = _model(1, "primary", "provider-a")
        attempt = _attempt(model, 1, "10")
        customer = SimpleNamespace(id="customer-1", amount_rub=Decimal("10"))
        provider = SimpleNamespace(id="provider-1")
        calls = iter([False, True])
        with (
            patch("apps.agents.dev_model_execution.plan_model_attempts", return_value=[attempt]),
            patch("apps.agents.dev_model_execution.reserve", return_value=customer),
            patch("apps.agents.dev_model_execution.reserve_agent_provider_spend", return_value=provider),
            patch("apps.agents.dev_model_execution.release") as release_customer,
            patch("apps.agents.dev_model_execution.release_agent_provider_spend") as release_provider,
        ):
            with self.assertRaises(DevStageCanceled):
                execute_with_model_fallback(
                    run=SimpleNamespace(id="run-id", owner=SimpleNamespace()),
                    sequence=1,
                    primary_model=model,
                    messages=[],
                    estimated_input_tokens=100,
                    requested_output_tokens=1000,
                    remaining_budget_rub=Decimal("20"),
                    is_canceled=lambda: next(calls),
                )

        release_customer.assert_called_once_with("customer-1")
        release_provider.assert_called_once_with(provider)
