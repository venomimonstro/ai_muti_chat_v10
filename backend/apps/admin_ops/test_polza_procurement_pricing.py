from decimal import Decimal

from django.test import SimpleTestCase

from .procurement_ledger_views import _per_million_from_generic, _polza_price_row


class PolzaProcurementPricingTests(SimpleTestCase):
    def test_parses_explicit_rub_per_million_prices(self):
        row = _polza_price_row(
            {
                "id": "openai/gpt-6-luna",
                "pricing_currency": "RUB",
                "input_rub_per_million": "5.9",
                "output_rub_per_million": "29.5",
                "image_output_rub_per_million": "6979.56",
            }
        )
        self.assertEqual(row["currency"], "RUB")
        self.assertEqual(row["input_per_million"], "5.9")
        self.assertEqual(row["output_per_million"], "29.5")
        self.assertEqual(row["image_output_per_million"], "6979.56")

    def test_parses_openai_compatible_per_token_pricing(self):
        row = _polza_price_row(
            {
                "id": "vendor/model",
                "pricing": {
                    "currency": "RUB",
                    "prompt": "0.0000059",
                    "completion": "0.0000295",
                    "image_input": "0.000001",
                    "image": "2.5",
                },
            }
        )
        self.assertEqual(Decimal(row["input_per_million"]), Decimal("5.9000000"))
        self.assertEqual(Decimal(row["output_per_million"]), Decimal("29.5000000"))
        self.assertEqual(Decimal(row["image_input_per_million"]), Decimal("1.000000"))
        self.assertEqual(row["image_per_image"], "2.5")

    def test_generic_price_above_one_is_treated_as_already_per_million(self):
        self.assertEqual(_per_million_from_generic(Decimal("5.9")), Decimal("5.9"))
