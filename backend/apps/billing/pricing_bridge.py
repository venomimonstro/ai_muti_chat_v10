from __future__ import annotations

from decimal import ROUND_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel

from .models import PriceVersion

ZERO = Decimal("0")
NATIVE_STEP = Decimal("0.000001")


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


def _provider_price_currency(model: AIModel) -> tuple[str, Decimal]:
    """Resolve the native currency and RUB/native FX for the funded credential.

    AIModel legacy cost fields are explicitly RUB-denominated. Provider procurement
    balances, however, are stored in the native currency of the default funding
    account (USD/EUR/RUB/etc.). The immutable PriceVersion must therefore use the
    same native currency as procurement or a funded provider would be rejected by
    strict runtime readiness despite having a valid key and positive balance.
    """
    try:
        account = (
            model.provider.funding_accounts.filter(active=True, is_default=True)
            .only("currency")
            .first()
        )
    except Exception:
        account = None
    currency = str(getattr(account, "currency", "") or "RUB").upper().strip()
    if not currency:
        currency = "RUB"
    if currency == "RUB":
        return "RUB", Decimal("1")

    # Reuse billing's governed FX snapshot (including staleness checks). This makes
    # the native price deterministic at bridge creation while the original RUB
    # legacy values remain preserved for operator visibility.
    from .pricing import active_fx_snapshot

    fx = active_fx_snapshot(currency)
    if fx is None or fx.rate <= ZERO:
        raise ValidationError(f"Не настроен корректный FX snapshot {currency}/RUB")
    return currency, Decimal(fx.rate)


@transaction.atomic
def ensure_price_from_model_cost(model_slug: str) -> PriceVersion | None:
    model = (
        AIModel.objects.select_for_update()
        .select_related("provider")
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

    input_cost_rub = Decimal(str(model.input_price_rub_per_million or 0))
    output_cost_rub = Decimal(str(model.output_price_rub_per_million or 0))
    if input_cost_rub <= ZERO or output_cost_rub <= ZERO:
        return None

    currency, rub_per_native = _provider_price_currency(model)
    input_native = (input_cost_rub / rub_per_native).quantize(
        NATIVE_STEP, rounding=ROUND_UP
    )
    output_native = (output_cost_rub / rub_per_native).quantize(
        NATIVE_STEP, rounding=ROUND_UP
    )
    if input_native <= ZERO or output_native <= ZERO:
        raise ValidationError("Не удалось преобразовать закупочную цену модели в валюту API-баланса")

    now = timezone.now()
    return PriceVersion.objects.create(
        model_slug=model.slug,
        # Keep the original operator-entered RUB values for display/backward
        # compatibility. Billing uses the native fields + governed FX snapshot.
        input_rub_per_million=input_cost_rub,
        output_rub_per_million=output_cost_rub,
        provider_currency=currency,
        input_price_per_million=input_native,
        output_price_per_million=output_native,
        markup_percent=Decimal("100"),
        active=True,
        effective_from=now,
    )
