from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .procurement_ledger_views import _polza_price_row, _pricing_snapshot_from_request


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


    @patch(
        "apps.admin_ops.procurement_ledger_views._polza_pricing_for_key",
        return_value=[
            {
                "id": "openai/gpt-test",
                "currency": "RUB",
                "input_per_million": "10",
                "output_per_million": "20",
                "image_input_per_million": "30",
                "image_output_per_million": "40",
                "image_per_image": "5",
                "pricing_tiers": [],
                "model_type": "chat",
            }
        ],
    )
    def test_manual_override_wins_and_source_is_recorded(self, _mock):
        key = SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            provider=SimpleNamespace(slug="polza"),
        )
        model = SimpleNamespace(
            slug="polza-gpt-test",
            upstream_model="openai/gpt-test",
        )
        request = SimpleNamespace(
            data={
                "input_per_million": "11.5",
                "output_per_million": "20",
                "image_per_image": "6",
                "pricing_currency": "RUB",
            }
        )
        snapshot = _pricing_snapshot_from_request(
            request,
            key=key,
            model=model,
        )
        self.assertEqual(snapshot["input_per_million"], "11.5")
        self.assertEqual(snapshot["output_per_million"], "20")
        self.assertEqual(snapshot["image_per_image"], "6")
        self.assertEqual(snapshot["sources"]["input_per_million"], "manual")
        self.assertEqual(snapshot["sources"]["output_per_million"], "auto")
        self.assertEqual(snapshot["sources"]["image_per_image"], "manual")
