from decimal import Decimal

from django.test import SimpleTestCase

from .procurement_ledger_views import _polza_price_row


class PolzaProcurementPricingTests(SimpleTestCase):
    def test_parses_official_catalog_token_and_image_prices(self):
        row = _polza_price_row(
            {
                "id": "openai/gpt-6-sol",
                "type": "chat",
                "top_provider": {
                    "pricing": {
                        "currency": "RUB",
                        "prompt_per_million": "117.992",
                        "completion_per_million": "589.96",
                        "image_input_per_million": "12.5",
                        "image_output_per_million": "6979.56",
                        "per_request": {},
                        "tiers": [],
                    }
                },
            }
        )
        self.assertEqual(row["currency"], "RUB")
        self.assertEqual(row["input_per_million"], "117.992")
        self.assertEqual(row["output_per_million"], "589.96")
        self.assertEqual(row["image_input_per_million"], "12.5")
        self.assertEqual(row["image_output_per_million"], "6979.56")
        self.assertEqual(row["image_per_image"], None)
        self.assertEqual(row["model_type"], "chat")

    def test_parses_per_request_image_price(self):
        row = _polza_price_row(
            {
                "id": "image/model",
                "type": "image",
                "top_provider": {
                    "pricing": {
                        "currency": "RUB",
                        "per_request": "2.5",
                    }
                },
            }
        )
        self.assertEqual(row["image_per_image"], "2.5")

    def test_preserves_image_price_tiers(self):
        row = _polza_price_row(
            {
                "id": "image/model",
                "type": "image",
                "top_provider": {
                    "pricing": {
                        "currency": "RUB",
                        "tiers": [
                            {
                                "conditions": ["image_resolution=4K"],
                                "cost_rub": "8.25",
                            }
                        ],
                    }
                },
            }
        )
        self.assertEqual(
            row["pricing_tiers"],
            [{"conditions": ["image_resolution=4K"], "cost_rub": "8.25"}],
        )

    def test_missing_prices_remain_empty_for_manual_override(self):
        row = _polza_price_row(
            {
                "id": "vendor/model",
                "type": "chat",
                "top_provider": {"pricing": {"currency": "RUB"}},
            }
        )
        self.assertIsNone(row["input_per_million"])
        self.assertIsNone(row["output_per_million"])
        self.assertIsNone(row["image_per_image"])
