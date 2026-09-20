from datetime import date
from decimal import Decimal

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import ensure_safe_client_models
from apps.billing.models import PriceVersion
from apps.procurement.official_pricing import (
    catalog_price,
    refresh_usd_rub_from_cbr,
    sync_official_prices,
)


# OpenAI's documentation can return HTTP 403 to datacenter/server requests even
# when the same official pages are publicly reachable in a browser/search index.
# These three values were manually re-verified against the official model pages on
# 2026-09-20. The fallback deliberately expires so stale prices can never remain a
# permanent silent source of truth.
OPENAI_REVIEWED_FALLBACK_UNTIL = date(2026, 10, 20)
OPENAI_REVIEWED_MODELS = {
    "gpt-5.6-luna": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    "gpt-5.6-terra": "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
    "gpt-5.6-sol": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
}


def _is_openai_docs_403(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        return bool(response is not None and response.status_code == 403)
    return "403 Forbidden" in str(exc)


@transaction.atomic
def _apply_reviewed_openai_fallback() -> dict:
    today = timezone.localdate()
    if today > OPENAI_REVIEWED_FALLBACK_UNTIL:
        raise CommandError(
            "OpenAI documentation still returns 403 and the reviewed pricing fallback has expired. "
            "Re-verify official prices before extending the fallback."
        )

    models = list(
        AIModel.objects.select_related("provider")
        .filter(provider__slug="openai", upstream_model__in=OPENAI_REVIEWED_MODELS)
        .order_by("slug")
    )
    if not models:
        return {
            "verified": [],
            "rejected": [],
            "unsupported": [],
            "expired": [],
            "usd_rub": None,
            "fx_source": "",
            "verification_mode": "reviewed_fallback",
        }

    fx = refresh_usd_rub_from_cbr()
    now = timezone.now()
    verified = []
    rejected = []

    for model in models:
        item = catalog_price("openai", model.upstream_model)
        if item is None:
            rejected.append(
                {
                    "model": model.slug,
                    "upstream_model": model.upstream_model,
                    "source_url": OPENAI_REVIEWED_MODELS[model.upstream_model],
                    "detail": "Модель отсутствует во встроенном проверенном каталоге",
                }
            )
            continue

        current = (
            PriceVersion.objects.filter(model_slug=model.slug, active=True)
            .order_by("-effective_from", "-created_at")
            .first()
        )
        markup = current.markup_percent if current else Decimal("100")

        # Reuse an already-current identical price instead of creating a new
        # version every time the repair command is run.
        if (
            current is not None
            and current.provider_currency == "USD"
            and current.input_price_per_million == item.input_usd_per_million
            and current.output_price_per_million == item.output_usd_per_million
        ):
            created = current
        else:
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

        verified.append(
            {
                "model": model.slug,
                "upstream_model": model.upstream_model,
                "price_version": str(created.id),
                "input_usd_per_million": str(item.input_usd_per_million),
                "output_usd_per_million": str(item.output_usd_per_million),
                "basis": f"reviewed_official_fallback_until_{OPENAI_REVIEWED_FALLBACK_UNTIL.isoformat()}",
                "source_url": OPENAI_REVIEWED_MODELS[model.upstream_model],
                "checked_at": now.isoformat(),
            }
        )

    return {
        "verified": verified,
        "rejected": rejected,
        "unsupported": [],
        "expired": [],
        "usd_rub": str(fx.rate),
        "fx_source": "https://www.cbr.ru/scripts/XML_daily.asp",
        "verification_mode": "reviewed_fallback",
    }


class Command(BaseCommand):
    help = (
        "Verify official provider pricing, create active PriceVersion rows for "
        "already discovered AI models, and optionally activate commercially safe models."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            default="",
            help="Provider slug to sync (for example: openai). Empty means all providers.",
        )
        parser.add_argument(
            "--activate-safe",
            action="store_true",
            help="After pricing sync, enable saved models that have a healthy key, active version and safe margin.",
        )

    def handle(self, *args, **options):
        provider = str(options.get("provider") or "").strip()
        try:
            result = sync_official_prices(provider_slug=provider)
        except Exception as exc:
            if provider == "openai" and _is_openai_docs_403(exc):
                self.stdout.write(
                    self.style.WARNING(
                        "OPENAI_DOCS_403: using time-limited reviewed official pricing fallback"
                    )
                )
                result = _apply_reviewed_openai_fallback()
            else:
                raise CommandError(f"Official pricing sync failed: {exc}") from exc

        verified = result.get("verified", [])
        rejected = result.get("rejected", [])
        unsupported = result.get("unsupported", [])
        expired = result.get("expired", [])

        self.stdout.write(
            f"USD_RUB={result.get('usd_rub') or '-'} source={result.get('fx_source') or '-'} "
            f"verification={result.get('verification_mode') or 'live_official'}"
        )
        for item in verified:
            self.stdout.write(
                self.style.SUCCESS(
                    "PRICE_OK "
                    f"model={item['model']} upstream={item['upstream_model']} "
                    f"input_usd={item['input_usd_per_million']} "
                    f"output_usd={item['output_usd_per_million']} "
                    f"price_version={item['price_version']}"
                )
            )
        for item in rejected:
            self.stdout.write(
                self.style.ERROR(
                    f"PRICE_REJECTED model={item['model']} upstream={item['upstream_model']} "
                    f"reason={item['detail']} source={item['source_url']}"
                )
            )
        for item in unsupported:
            self.stdout.write(
                self.style.WARNING(
                    f"PRICE_UNSUPPORTED model={item['model']} upstream={item['upstream_model']} "
                    f"provider={item['provider']}"
                )
            )
        for item in expired:
            self.stdout.write(
                self.style.WARNING(
                    f"PRICE_EXPIRED model={item['model']} upstream={item['upstream_model']} "
                    f"effective_until={item['effective_until']}"
                )
            )

        activated = 0
        if options.get("activate_safe"):
            activated = ensure_safe_client_models()
            self.stdout.write(self.style.SUCCESS(f"SAFE_MODELS_ACTIVATED={activated}"))

        self.stdout.write(
            f"VERIFIED={len(verified)} REJECTED={len(rejected)} "
            f"UNSUPPORTED={len(unsupported)} EXPIRED={len(expired)}"
        )
        if not verified:
            raise CommandError(
                "No official prices were applied. Check PRICE_REJECTED/PRICE_UNSUPPORTED lines above."
            )
