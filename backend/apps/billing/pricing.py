from decimal import ROUND_UP, Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import PriceVersion

MILLION = Decimal("1000000")
MONEY_STEP = Decimal("0.0001")


def active_price(model_slug: str) -> PriceVersion:
    price = (
        PriceVersion.objects.filter(
            model_slug=model_slug, active=True, effective_from__lte=timezone.now()
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if not price:
        raise ValidationError("Для выбранной модели не настроена цена")
    return price


def calculate(price: PriceVersion, input_tokens: int, output_tokens: int):
    provider_cost = (
        Decimal(input_tokens) * price.input_rub_per_million
        + Decimal(output_tokens) * price.output_rub_per_million
    ) / MILLION
    charge = provider_cost * (Decimal("1") + price.markup_percent / Decimal("100"))
    return provider_cost.quantize(MONEY_STEP, rounding=ROUND_UP), charge.quantize(
        MONEY_STEP, rounding=ROUND_UP
    )


def conservative_token_budget(messages: list[dict], max_output_tokens: int):
    # One Unicode character per token is deliberately conservative until a model tokenizer is added.
    input_budget = sum(len(item.get("content", "")) for item in messages) + 32
    return input_budget, max_output_tokens
