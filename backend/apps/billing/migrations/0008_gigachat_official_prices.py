from decimal import Decimal

from django.db import migrations
from django.utils import timezone


# Official synchronous GigaChat API prices reviewed 2026-09-23:
# Lite 65 RUB / 1M tokens, Pro 500 RUB / 1M, Max 650 RUB / 1M.
PRICES = {
    "GigaChat": Decimal("65"),
    "GigaChat-2": Decimal("65"),
    "GigaChat-Pro": Decimal("500"),
    "GigaChat-2-Pro": Decimal("500"),
    "GigaChat-Max": Decimal("650"),
    "GigaChat-2-Max": Decimal("650"),
}


def seed_gigachat_prices(apps, schema_editor):
    AIModel = apps.get_model("ai_registry", "AIModel")
    PriceVersion = apps.get_model("billing", "PriceVersion")
    now = timezone.now()
    for model in AIModel.objects.filter(provider__slug="gigachat").exclude(upstream_model=""):
        cost = PRICES.get(model.upstream_model)
        if cost is None:
            continue
        existing = PriceVersion.objects.filter(model_slug=model.slug, active=True).first()
        if existing is not None:
            continue
        PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=cost,
            output_rub_per_million=cost,
            provider_currency="RUB",
            input_price_per_million=cost,
            output_price_per_million=cost,
            markup_percent=Decimal("100"),
            active=True,
            effective_from=now,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0011_provider_auth_config"),
        ("billing", "0007_pricing_overhead_policy"),
    ]

    operations = [migrations.RunPython(seed_gigachat_prices, migrations.RunPython.noop)]
