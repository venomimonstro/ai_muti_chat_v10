from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import FxRateSnapshot, PriceVersion

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


@dataclass(frozen=True)
class OfficialPrice:
    provider: str
    model_id: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    source_url: str
    basis: str = "standard"
    note: str = ""
    effective_until: date | None = None


OPENAI_PRICING = "https://developers.openai.com/api/docs/models"
OPENAI_REVIEWED_FALLBACK_UNTIL = date(2026, 10, 20)
ANTHROPIC_SONNET5 = "https://www.anthropic.com/news/claude-sonnet-5"
ANTHROPIC_OPUS5 = "https://www.anthropic.com/claude/opus"
ANTHROPIC_HAIKU45 = "https://www.anthropic.com/news/claude-haiku-4-5"
ANTHROPIC_OPUS48 = "https://www.anthropic.com/news/claude-opus-4-8"
ANTHROPIC_SONNET46 = "https://www.anthropic.com/news/claude-sonnet-4-6"
DEEPSEEK_PRICING = "https://api-docs.deepseek.com/quick_start/pricing/"
GEMINI_PRICING = "https://ai.google.dev/gemini-api/docs/pricing"
XAI_PRICING = "https://docs.x.ai/developers/pricing"

# The values below are a guarded parser baseline. Normally the official source is
# downloaded and the model id plus both price values must be visible near that model.
# OpenAI currently may return HTTP 403 to server-side documentation fetches, so for
# that source only we allow a short-lived, explicitly reviewed catalog fallback.
OFFICIAL_CATALOG: dict[tuple[str, str], OfficialPrice] = {}


def _add(provider, model_id, input_price, output_price, source, **kwargs):
    OFFICIAL_CATALOG[(provider, model_id)] = OfficialPrice(
        provider=provider,
        model_id=model_id,
        input_usd_per_million=Decimal(str(input_price)),
        output_usd_per_million=Decimal(str(output_price)),
        source_url=source,
        **kwargs,
    )


# OpenAI standard short-context token pricing.
_add("openai", "gpt-6-astra", 10, 50, OPENAI_PRICING)
_add("openai", "gpt-5.6-sol", 4, 20, OPENAI_PRICING)
_add("openai", "gpt-5.6", 4, 20, OPENAI_PRICING)
_add("openai", "gpt-5.6-terra", 2, 12, OPENAI_PRICING)
_add("openai", "gpt-5.6-luna", 0.20, 1.20, OPENAI_PRICING)

# Anthropic standard global pricing.
_add("anthropic", "claude-sonnet-5", 2, 10, ANTHROPIC_SONNET5)
_add("anthropic", "claude-opus-5", 5, 25, ANTHROPIC_OPUS5)
_add("anthropic", "claude-opus-4-8", 5, 25, ANTHROPIC_OPUS48)
_add("anthropic", "claude-sonnet-4-6", 3, 15, ANTHROPIC_SONNET46)
_add("anthropic", "claude-haiku-4-5", 1, 5, ANTHROPIC_HAIKU45)

# DeepSeek has peak/off-peak prices. We deliberately store PEAK cache-miss input
# and PEAK output so a request cannot become loss-making when the clock enters peak.
_add("deepseek", "deepseek-flash", 0.30, 1.20, DEEPSEEK_PRICING, basis="peak_cache_miss", note="Защитная цена: peak, cache miss")
_add("deepseek", "deepseek-v4-pro", 1.32, 3.96, DEEPSEEK_PRICING, basis="peak_cache_miss", note="Защитная цена: peak, cache miss")

# Gemini 3.8/3.7/3.6 promotional standard prices are published through 2026-12-31.
_google_until = date(2026, 12, 31)
for _mid in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"):
    _add("gemini", _mid, 0.75, 3.75, GEMINI_PRICING, effective_until=_google_until, note="Standard paid tier through 2026-12-31")
_add("gemini", "gemini-3.5-flash", 1.50, 9.00, GEMINI_PRICING)
_add("gemini", "gemini-3.5-flash-lite", 0.30, 2.50, GEMINI_PRICING)
_add("gemini", "gemini-2.5-flash", 0.30, 2.50, GEMINI_PRICING)
_add("gemini", "gemini-2.5-flash-lite", 0.10, 0.40, GEMINI_PRICING)
_add("gemini", "gemini-2.5-pro", 2.50, 15.00, GEMINI_PRICING, basis="max_standard_tier", note="Защитная ставка для context >200k")

# xAI has higher rates once prompt context reaches 200k. Current billing schema is
# single-rate, so use long-context rates for affected models to fail safe.
_add("xai", "grok-4.6", 4.00, 12.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")
_add("xai", "grok-4.5", 4.00, 12.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")
_add("xai", "grok-4.3", 2.50, 5.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")
_add("xai", "grok-4.20", 2.50, 5.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")
_add("xai", "grok-4.20-0309-reasoning", 2.50, 5.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")
_add("xai", "grok-4.20-0309-non-reasoning", 2.50, 5.00, XAI_PRICING, basis="long_context", note="Защитная ставка >=200k context")


def source_for(provider_slug: str, model_id: str) -> str:
    item = OFFICIAL_CATALOG.get((provider_slug, model_id))
    return item.source_url if item else ""


def catalog_price(provider_slug: str, model_id: str) -> OfficialPrice | None:
    return OFFICIAL_CATALOG.get((provider_slug, model_id))


def _plain_text(raw: str) -> str:
    raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _decimal_tokens(value: Decimal) -> set[str]:
    normalized = format(value.normalize(), "f")
    fixed = format(value, "f")
    return {normalized, fixed, f"${normalized}", f"${fixed}"}


def _verify_visible(text: str, item: OfficialPrice) -> bool:
    lower = text.lower()
    model = item.model_id.lower()
    pos = lower.find(model)
    if pos < 0:
        display = model.replace("-", " ")
        pos = lower.find(display)
    if pos < 0:
        return False
    window = lower[max(0, pos - 600): pos + 6000]
    in_ok = any(token.lower() in window for token in _decimal_tokens(item.input_usd_per_million))
    out_ok = any(token.lower() in window for token in _decimal_tokens(item.output_usd_per_million))
    return in_ok and out_ok


def _fetch_sources(items: list[OfficialPrice]) -> tuple[dict[str, str], set[str]]:
    result: dict[str, str] = {}
    reviewed_fallback_urls: set[str] = set()
    headers = {"User-Agent": "AIWorkspace-PricingVerifier/1.0 (+admin pricing sync)"}
    today = timezone.localdate()
    for url in sorted({x.source_url for x in items}):
        try:
            response = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
            response.raise_for_status()
            result[url] = _plain_text(response.text)
        except httpx.HTTPStatusError as exc:
            if (
                url == OPENAI_PRICING
                and exc.response.status_code == 403
                and today <= OPENAI_REVIEWED_FALLBACK_UNTIL
            ):
                # OpenAI documentation can reject datacenter/server fetches while the
                # same public page remains available in a browser. We do not turn this
                # into a permanent bypass: only the reviewed catalog is accepted and
                # only until the explicit expiry date above.
                result[url] = ""
                reviewed_fallback_urls.add(url)
                continue
            raise
    return result, reviewed_fallback_urls


def refresh_usd_rub_from_cbr() -> FxRateSnapshot:
    response = httpx.get(CBR_DAILY_URL, timeout=15, follow_redirects=True)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    usd = None
    for item in root.findall("Valute"):
        if (item.findtext("CharCode") or "").strip() == "USD":
            nominal = Decimal((item.findtext("Nominal") or "1").replace(",", "."))
            value = Decimal((item.findtext("Value") or "0").replace(",", "."))
            if nominal > 0 and value > 0:
                usd = value / nominal
            break
    if usd is None:
        raise ValidationError("ЦБ РФ не вернул курс USD/RUB")
    return FxRateSnapshot.objects.create(
        base_currency="USD",
        quote_currency="RUB",
        rate=usd,
        source="cbr.ru",
        source_reference=CBR_DAILY_URL,
        effective_at=timezone.now(),
    )


def official_status(model: AIModel) -> dict:
    item = catalog_price(model.provider.slug, model.upstream_model)
    return {
        "supported": item is not None,
        "source_url": item.source_url if item else "",
        "basis": item.basis if item else "",
        "note": item.note if item else "",
        "effective_until": item.effective_until.isoformat() if item and item.effective_until else None,
    }


@transaction.atomic
def sync_official_prices(*, provider_slug: str = "") -> dict:
    models = AIModel.objects.select_related("provider").exclude(upstream_model="")
    if provider_slug:
        models = models.filter(provider__slug=provider_slug)
    selected: list[tuple[AIModel, OfficialPrice]] = []
    unsupported = []
    expired = []
    today = timezone.localdate()
    for model in models:
        item = catalog_price(model.provider.slug, model.upstream_model)
        if item is None:
            unsupported.append({"model": model.slug, "upstream_model": model.upstream_model, "provider": model.provider.slug})
            continue
        if item.effective_until and today > item.effective_until:
            expired.append({"model": model.slug, "upstream_model": model.upstream_model, "effective_until": item.effective_until.isoformat()})
            continue
        selected.append((model, item))

    source_text, reviewed_fallback_urls = _fetch_sources([item for _, item in selected]) if selected else ({}, set())
    verified = []
    rejected = []
    now = timezone.now()
    fx = refresh_usd_rub_from_cbr() if selected else None

    for model, item in selected:
        text = source_text.get(item.source_url, "")
        using_reviewed_fallback = item.source_url in reviewed_fallback_urls
        if not using_reviewed_fallback and not _verify_visible(text, item):
            rejected.append({
                "model": model.slug,
                "upstream_model": item.model_id,
                "source_url": item.source_url,
                "detail": "Официальная страница изменилась или цена не подтверждена",
            })
            continue
        previous = PriceVersion.objects.filter(model_slug=model.slug, active=True).order_by("-effective_from", "-created_at").first()
        markup = previous.markup_percent if previous else Decimal("100")
        PriceVersion.objects.filter(model_slug=model.slug, active=True).update(active=False)
        created = PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=(item.input_usd_per_million * fx.rate).quantize(Decimal("0.0001")),
            output_rub_per_million=(item.output_usd_per_million * fx.rate).quantize(Decimal("0.0001")),
            provider_currency="USD",
            input_price_per_million=item.input_usd_per_million,
            output_price_per_million=item.output_usd_per_million,
            markup_percent=markup,
            active=True,
            effective_from=now,
        )
        verified.append({
            "model": model.slug,
            "upstream_model": item.model_id,
            "price_version": str(created.id),
            "input_usd_per_million": str(item.input_usd_per_million),
            "output_usd_per_million": str(item.output_usd_per_million),
            "basis": item.basis,
            "source_url": item.source_url,
            "checked_at": now.isoformat(),
            "verification": "reviewed_fallback" if using_reviewed_fallback else "live_official_page",
            "fallback_expires": OPENAI_REVIEWED_FALLBACK_UNTIL.isoformat() if using_reviewed_fallback else None,
        })
    return {
        "verified": verified,
        "rejected": rejected,
        "unsupported": unsupported,
        "expired": expired,
        "usd_rub": str(fx.rate) if fx else None,
        "fx_source": CBR_DAILY_URL if fx else "",
        "verification": "reviewed_fallback" if reviewed_fallback_urls else "live_official_pages",
        "reviewed_fallback_urls": sorted(reviewed_fallback_urls),
        "openai_fallback_expires": OPENAI_REVIEWED_FALLBACK_UNTIL.isoformat() if OPENAI_PRICING in reviewed_fallback_urls else None,
    }
