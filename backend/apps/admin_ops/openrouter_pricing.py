from __future__ import annotations

from decimal import Decimal, InvalidOperation

import httpx
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.pricing import active_fx_snapshot
from apps.procurement.official_pricing import refresh_usd_rub_from_cbr

MILLION = Decimal("1000000")
USD_PRICE_STEP = Decimal("0.000001")
RUB_PRICE_STEP = Decimal("0.0001")
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _usd_rub_snapshot():
    try:
        return active_fx_snapshot("USD")
    except ValidationError:
        return refresh_usd_rub_from_cbr()


def _catalog(provider: Provider) -> dict[str, dict]:
    api_key = provider.get_api_key()
    if not api_key:
        raise ValidationError("OpenRouter API-ключ не настроен")
    url = f"{provider.api_base_url.rstrip('/')}/models"
    response = httpx.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        str(item.get("id") or "").strip(): item
        for item in (payload.get("data") or [])
        if str(item.get("id") or "").strip()
    }


def sync_openrouter_prices(provider: Provider, model_ids: list[str]) -> dict:
    """Create immutable PriceVersion rows from OpenRouter's live model catalog.

    OpenRouter publishes `prompt` and `completion` as USD per token. Billing
    stores the authoritative provider rate in USD per million tokens plus the
    contemporaneous USD/RUB snapshot. Existing markup is preserved.
    """
    if provider.slug != "openrouter":
        return {"verified": [], "rejected": [], "skipped": True}

    wanted = {str(value).strip() for value in model_ids if str(value).strip()}
    if not wanted:
        return {"verified": [], "rejected": [], "skipped": False}

    catalog = _catalog(provider)
    fx = _usd_rub_snapshot()
    now = timezone.now()
    models = {
        item.upstream_model: item
        for item in AIModel.objects.filter(provider=provider, upstream_model__in=wanted)
    }
    verified = []
    rejected = []

    for upstream in sorted(wanted):
        model = models.get(upstream)
        remote = catalog.get(upstream)
        if model is None:
            rejected.append({"model": upstream, "detail": "Модель не сохранена в локальном каталоге"})
            continue
        if remote is None:
            rejected.append({"model": upstream, "detail": "OpenRouter больше не возвращает эту модель"})
            continue

        pricing = remote.get("pricing") if isinstance(remote.get("pricing"), dict) else {}
        prompt_per_token = _decimal(pricing.get("prompt"))
        completion_per_token = _decimal(pricing.get("completion"))
        if prompt_per_token is None or completion_per_token is None:
            rejected.append({"model": upstream, "detail": "OpenRouter не вернул token pricing"})
            continue
        if prompt_per_token < 0 or completion_per_token < 0:
            rejected.append({"model": upstream, "detail": "OpenRouter вернул некорректную отрицательную цену"})
            continue
        if prompt_per_token == 0 and completion_per_token == 0:
            rejected.append({
                "model": upstream,
                "detail": "Бесплатная модель требует отдельного zero-cost billing режима; коммерческий чат её автоматически не активирует",
            })
            continue

        input_usd = (prompt_per_token * MILLION).quantize(USD_PRICE_STEP)
        output_usd = (completion_per_token * MILLION).quantize(USD_PRICE_STEP)
        previous = (
            PriceVersion.objects.filter(model_slug=model.slug, active=True)
            .order_by("-effective_from", "-created_at")
            .first()
        )
        markup = previous.markup_percent if previous else Decimal("100")

        if (
            previous
            and previous.provider_currency == "USD"
            and previous.input_price_per_million == input_usd
            and previous.output_price_per_million == output_usd
        ):
            created = previous
            changed = False
        else:
            PriceVersion.objects.filter(model_slug=model.slug, active=True).update(active=False)
            created = PriceVersion.objects.create(
                model_slug=model.slug,
                input_rub_per_million=(input_usd * fx.rate).quantize(RUB_PRICE_STEP),
                output_rub_per_million=(output_usd * fx.rate).quantize(RUB_PRICE_STEP),
                provider_currency="USD",
                input_price_per_million=input_usd,
                output_price_per_million=output_usd,
                markup_percent=markup,
                active=True,
                effective_from=now,
            )
            changed = True

        context_length = remote.get("context_length")
        try:
            context_length = int(context_length or 0)
        except (TypeError, ValueError):
            context_length = 0
        if context_length > 0 and model.context_window != context_length:
            model.context_window = context_length
            model.save(update_fields=["context_window"])

        verified.append(
            {
                "model": model.slug,
                "upstream_model": upstream,
                "price_version": str(created.id),
                "input_usd_per_million": str(input_usd),
                "output_usd_per_million": str(output_usd),
                "usd_rub": str(fx.rate),
                "changed": changed,
                "source_url": OPENROUTER_MODELS_URL,
            }
        )

    return {
        "verified": verified,
        "rejected": rejected,
        "skipped": False,
        "usd_rub": str(fx.rate),
        "source_url": OPENROUTER_MODELS_URL,
    }
