from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel

from .models import PriceVersion

ZERO = Decimal("0")


def install(pricing_module) -> None:
    """Install a backwards-compatible commercial pricing bridge.

    Operators historically configured provider token cost directly on AIModel.
    Runtime billing now uses immutable PriceVersion rows. Without this bridge a
    healthy, funded provider could remain invisible merely because the same cost
    had not been copied into a second table.
    """
    if getattr(pricing_module.active_price, "_ai_workspace_pricing_bridge", False):
        return
    raw_active_price = pricing_module.active_price

    def active_price(model_slug: str):
        try:
            return raw_active_price(model_slug)
        except ValidationError as original_error:
            created = ensure_price_from_model_cost(model_slug)
            if created is None:
                raise original_error
            return created

    active_price._ai_workspace_pricing_bridge = True
    active_price._raw_active_price = raw_active_price
    pricing_module.active_price = active_price

    # ai_registry.router imports active_price at module import time. If it is
    # already loaded, rebind its local reference as well. Modules imported later
    # receive pricing_module.active_price automatically.
    import sys

    router = sys.modules.get("apps.ai_registry.router")
    if router is not None:
        router.active_price = active_price


@transaction.atomic
def ensure_price_from_model_cost(model_slug: str) -> PriceVersion | None:
    model = (
        AIModel.objects.select_for_update()
        .filter(slug=model_slug, enabled=True)
        .first()
    )
    if model is None:
        return None

    existing = (
        PriceVersion.objects.filter(
            model_slug=model.slug,
            active=True,
            effective_from__lte=timezone.now(),
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if existing is not None:
        return existing

    input_cost = Decimal(str(model.input_price_rub_per_million or 0))
    output_cost = Decimal(str(model.output_price_rub_per_million or 0))
    if input_cost <= ZERO or output_cost <= ZERO:
        return None

    now = timezone.now()
    return PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=input_cost,
        output_rub_per_million=output_cost,
        provider_currency="RUB",
        input_price_per_million=input_cost,
        output_price_per_million=output_cost,
        markup_percent=Decimal("100"),
        active=True,
        effective_from=now,
    )
