from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .dev_model_fallback import plan_model_attempts


def _model(pk, slug, provider, max_output=4000):
    return SimpleNamespace(
        pk=pk,
        slug=slug,
        upstream_model=f"upstream-{slug}",
        max_output_tokens=max_output,
        provider=SimpleNamespace(slug=provider),
    )


class DevModelFallbackPlannerTests(SimpleTestCase):
    def test_each_candidate_gets_its_own_price_quote(self):
        primary = _model(1, "primary", "provider-a")
        fallback = _model(2, "fallback", "provider-b", max_output=2000)

        def quoted(_price, _input, output, *, provider_slug, model_slug, operation_type):
            assert operation_type == "agent"
            charge = Decimal("10") if model_slug == "primary" else Decimal("12")
            return SimpleNamespace(user_charge_rub=charge, model=model_slug, output=output, provider=provider_slug)

        with (
            patch("apps.agents.dev_model_fallback.candidate_models", return_value=[primary, fallback]),
            patch("apps.agents.dev_model_fallback.active_price", side_effect=lambda slug: f"price:{slug}"),
            patch("apps.agents.dev_model_fallback.quote", side_effect=quoted) as quote_mock,
            patch("apps.agents.dev_model_fallback.require_margin", side_effect=lambda value: value),
        ):
            attempts = plan_model_attempts(
                primary_model=primary,
                estimated_input_tokens=1000,
                requested_output_tokens=3000,
                remaining_budget_rub=Decimal("20"),
            )

        self.assertEqual([item.model.slug for item in attempts], ["primary", "fallback"])
        self.assertEqual(attempts[1].output_tokens, 2000)
        self.assertEqual(attempts[1].estimated_charge_rub, Decimal("12"))
        self.assertGreaterEqual(quote_mock.call_count, 3)  # primary baseline + per-candidate quotes

    def test_fallback_above_price_multiplier_is_rejected(self):
        primary = _model(1, "primary", "provider-a")
        expensive = _model(2, "expensive", "provider-b")

        def quoted(_price, _input, _output, *, model_slug, **_kwargs):
            return SimpleNamespace(user_charge_rub=Decimal("10") if model_slug == "primary" else Decimal("20"))

        with (
            patch("apps.agents.dev_model_fallback.candidate_models", return_value=[primary, expensive]),
            patch("apps.agents.dev_model_fallback.active_price", return_value=SimpleNamespace()),
            patch("apps.agents.dev_model_fallback.quote", side_effect=quoted),
            patch("apps.agents.dev_model_fallback.require_margin", side_effect=lambda value: value),
        ):
            attempts = plan_model_attempts(
                primary_model=primary,
                estimated_input_tokens=1000,
                requested_output_tokens=1000,
                remaining_budget_rub=Decimal("100"),
            )

        self.assertEqual([item.model.slug for item in attempts], ["primary"])

    def test_candidate_over_remaining_budget_is_rejected(self):
        primary = _model(1, "primary", "provider-a")
        fallback = _model(2, "fallback", "provider-b")

        def quoted(_price, _input, _output, *, model_slug, **_kwargs):
            return SimpleNamespace(user_charge_rub=Decimal("7") if model_slug == "primary" else Decimal("8"))

        with (
            patch("apps.agents.dev_model_fallback.candidate_models", return_value=[primary, fallback]),
            patch("apps.agents.dev_model_fallback.active_price", return_value=SimpleNamespace()),
            patch("apps.agents.dev_model_fallback.quote", side_effect=quoted),
            patch("apps.agents.dev_model_fallback.require_margin", side_effect=lambda value: value),
        ):
            attempts = plan_model_attempts(
                primary_model=primary,
                estimated_input_tokens=1000,
                requested_output_tokens=1000,
                remaining_budget_rub=Decimal("7.50"),
            )

        self.assertEqual([item.model.slug for item in attempts], ["primary"])

    def test_no_available_candidate_returns_empty_plan(self):
        primary = _model(1, "primary", "provider-a")
        with (
            patch("apps.agents.dev_model_fallback.candidate_models", return_value=[]),
            patch("apps.agents.dev_model_fallback.active_price", return_value=SimpleNamespace()),
            patch("apps.agents.dev_model_fallback.quote", return_value=SimpleNamespace(user_charge_rub=Decimal("10"))),
            patch("apps.agents.dev_model_fallback.require_margin", side_effect=lambda value: value),
        ):
            attempts = plan_model_attempts(
                primary_model=primary,
                estimated_input_tokens=1000,
                requested_output_tokens=1000,
                remaining_budget_rub=Decimal("100"),
            )
        self.assertEqual(attempts, [])
