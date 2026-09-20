from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.billing.cost_policy import PricingOverheadPolicyVersion
from apps.billing.models import FxRateSnapshot, MarkupRuleVersion, PriceVersion
from apps.billing.pricing import quote


class PricingOverheadTests(TestCase):
    def setUp(self):
        FxRateSnapshot.objects.create(
            base_currency="USD",
            quote_currency="RUB",
            rate=Decimal("100"),
            source="test",
            effective_at=timezone.now(),
        )
        self.price = PriceVersion.objects.create(
            model_slug="test-model",
            input_rub_per_million=Decimal("100"),
            output_rub_per_million=Decimal("200"),
            provider_currency="USD",
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("2"),
            markup_percent=Decimal("50"),
            active=True,
            effective_from=timezone.now(),
        )
        MarkupRuleVersion.objects.create(
            scope_type=MarkupRuleVersion.Scope.MODEL,
            scope_key="test-model",
            markup_percent=Decimal("50"),
            active=True,
            effective_from=timezone.now(),
        )
        PricingOverheadPolicyVersion.objects.create(
            tax_percent=Decimal("6"),
            topup_fee_percent=Decimal("3"),
            other_expenses_percent=Decimal("2"),
            refund_withdrawal_percent=Decimal("1"),
            active=True,
            effective_from=timezone.now(),
        )

    def test_overheads_are_added_to_sale_but_not_profit(self):
        q = quote(self.price, 1_000_000, 0, model_slug="test-model")
        self.assertEqual(q.provider_cost_rub, Decimal("100.0000"))
        self.assertEqual(q.economic_cost_rub, Decimal("112.0000"))
        self.assertEqual(q.user_charge_rub, Decimal("162.0000"))
        self.assertEqual(q.gross_profit_rub, Decimal("50.0000"))
        self.assertEqual(Decimal(q.pricing_snapshot["overhead_total_percent"]), Decimal("12"))
        self.assertEqual(Decimal(q.pricing_snapshot["tax_percent"]), Decimal("6"))
        self.assertEqual(Decimal(q.pricing_snapshot["topup_fee_percent"]), Decimal("3"))
        self.assertEqual(Decimal(q.pricing_snapshot["other_expenses_percent"]), Decimal("2"))
        self.assertEqual(Decimal(q.pricing_snapshot["refund_withdrawal_percent"]), Decimal("1"))
