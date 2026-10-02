from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .ai_planner_views import AgentAIPlannerPreviewView


class _Operation:
    def __init__(self):
        self.response = {}
        self.saved = 0

    def save(self, **_kwargs):
        self.saved += 1


class PlannerBillingSafetyTests(SimpleTestCase):
    def test_invalid_provider_draft_never_charges_customer(self):
        view = AgentAIPlannerPreviewView()
        request = SimpleNamespace(
            user=SimpleNamespace(),
            data={
                "description": (
                    "Создай AI сотрудника для подготовки контента, исследования "
                    "и безопасного согласования публикаций"
                )
            },
        )
        model = SimpleNamespace(
            slug="planner-model",
            upstream_model="planner-upstream",
            max_output_tokens=4096,
            provider=SimpleNamespace(slug="planner-provider"),
        )
        preflight = SimpleNamespace(
            user_charge_rub=Decimal("2.0000"),
            provider_cost_rub=Decimal("1.0000"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
            pricing_snapshot={"provider_currency": "USD"},
        )
        actual = SimpleNamespace(
            user_charge_rub=Decimal("1.5000"),
            provider_cost_rub=Decimal("0.8000"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        )
        customer = SimpleNamespace(id="customer-reservation", amount_rub=Decimal("2.0000"))
        provider_reservation = SimpleNamespace(
            id="provider-reservation",
            account_id="funding-account",
        )
        provider_spend = SimpleNamespace(id="provider-spend")

        class Adapter:
            def generate(self, **_kwargs):
                return SimpleNamespace(
                    text="not-json",
                    input_tokens=100,
                    output_tokens=20,
                    provider_request_id="provider-request",
                )

        operation = _Operation()
        with (
            patch("apps.agents.ai_planner_views._model_for", return_value=model),
            patch("apps.agents.ai_planner_views.estimate_message_tokens", return_value=100),
            patch("apps.agents.ai_planner_views.active_price", return_value=object()),
            patch("apps.agents.ai_planner_views.quote", return_value=preflight),
            patch(
                "apps.agents.ai_planner_views.require_margin",
                side_effect=lambda value: value,
            ),
            patch("apps.agents.ai_planner_views.reserve", return_value=customer),
            patch(
                "apps.agents.ai_planner_views.reserve_agent_provider_spend",
                return_value=provider_reservation,
            ),
            patch(
                "apps.agents.ai_planner_views.dispatch.adapter_for",
                return_value=Adapter(),
            ),
            patch(
                "apps.agents.ai_planner_views.actual_agent_quote_from_snapshot",
                return_value=actual,
            ),
            patch(
                "apps.agents.ai_planner_views.settle_agent_provider_spend",
                return_value=provider_spend,
            ) as settle_provider,
            patch("apps.agents.ai_planner_views.settle") as settle_customer,
            patch("apps.agents.ai_planner_views.release") as release_customer,
            patch(
                "apps.agents.ai_planner_views.release_agent_provider_spend"
            ) as release_provider,
        ):
            with self.assertRaises(ValidationError):
                view._generate(request, operation=operation)

        settle_provider.assert_called_once()
        self.assertEqual(
            settle_provider.call_args.kwargs["customer_charge"],
            Decimal("0"),
        )
        settle_customer.assert_not_called()
        release_customer.assert_not_called()
        release_provider.assert_not_called()
        checkpoint = operation.response["_provider_settlement"]
        self.assertEqual(checkpoint["status"], "pending")
        self.assertEqual(checkpoint["provider_request_id"], "provider-request")
